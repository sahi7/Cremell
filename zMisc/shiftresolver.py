from celery import shared_task
from django.db import models
from django.core.cache import caches
from django.utils import timezone
from datetime import date, timedelta
from django.core.cache import cache
from redis.asyncio import Redis
from dateutil.rrule import rrule, DAILY

from collections import defaultdict
from typing import Optional, Union
from notifications.models import ShiftAssignmentLog
from CRE.models import Branch, StaffShift, ShiftPattern, CustomUser
import logging

logger = logging.getLogger(__name__)

# For date_range() implementation
def date_range(start_date, end_date):
    return [dt.date() for dt in rrule(DAILY, dtstart=start_date, until=end_date)]

class ShiftResolver:
    def __init__(self, branch_id: int):
        self.branch_id = branch_id
        self.cache = caches['shift_resolver']
        self.cache_key = f'shift_patterns:{branch_id}'
    
    async def preload_patterns(self):
        """Cache all active patterns for branch"""
        patterns = await ShiftPattern.objects.filter(
            branch_id=self.branch_id,
            active_from__lte=timezone.now().date() + timedelta(days=14),
            active_until__gte=timezone.now().date() | models.Q(active_until__isnull=True)
        ).select_related('user').order_by('-priority').alist()
        
        await self.cache.aset(
            self.cache_key,
            patterns,
            timeout=3600  # 1 hour cache
        )
        return patterns
    
    async def resolve_shift(self, user: CustomUser, date: date) -> Optional[int]:
        """Resolve shift assignment with fallthrough logic"""
        # Verify user belongs to this branch
        if not await user.branches.filter(id=self.branch_id).aexists():
            return None
        # Check cache first
        if not (patterns := await self.cache.aget(self.cache_key)):
            patterns = await self.preload_patterns()
        
        # Filter applicable patterns (user-specific > role-based)
        user_patterns = [
            p for p in patterns 
            if (p.user_id == user.id) or 
               (p.role == user.role and not p.user_id)
        ]
        
        for pattern in user_patterns:
            if shift_id := self._apply_pattern(pattern, date, user.role):
                return shift_id
        return None
    
    def _apply_pattern(self, pattern: ShiftPattern, date: date, user_role: str) -> Optional[int]:
        """Comprehensive pattern resolver with all configuration types"""
        # Date validity check (optimized)
        if date < pattern.active_from or (pattern.active_until and date > pattern.active_until):
            return None

        config = pattern.config
        pattern_type = pattern.pattern_type
        weekday = date.strftime("%a").upper()  # MON, TUE, etc.
        
        try:
            if pattern_type == ShiftPattern.PatternType.ROLE_BASED:
                return self._resolve_role_based(config, weekday)
                
            elif pattern_type == ShiftPattern.PatternType.USER_SPECIFIC:
                return self._resolve_user_specific(config, weekday)
                
            elif pattern_type == ShiftPattern.PatternType.ROTATING:
                return self._resolve_rotating(config, date, pattern.active_from)
                
            elif pattern_type == ShiftPattern.PatternType.AD_HOC:
                return self._resolve_ad_hoc(config, date, user_role)
                
            elif pattern_type == ShiftPattern.PatternType.HYBRID:
                return self._resolve_hybrid(config, date, user_role, pattern.active_from)
                
        except (KeyError, TypeError, IndexError) as e:
            logger.error(f"Pattern resolution error: {e} for pattern {pattern.id}")
            return None
            
        return None

    def _resolve_role_based(self, config: dict, weekday: str) -> Optional[int]:
        """Role-based pattern resolution"""
        # Check exceptions first
        exceptions = config.get("exceptions", {})
        if weekday in exceptions.get("days", []):
            return exceptions["shift"]
        
        # Default shift fallback
        return config.get("default_shift")

    def _resolve_user_specific(self, config: dict, weekday: str) -> Optional[int]:
        """User-specific pattern resolution"""
        for entry in config.get("fixed_schedule", []):
            if entry.get("day", "").upper() == weekday:
                shift = entry.get("shift")
                return None if shift == "OFF" else shift
        return None

    def _resolve_rotating(self, config: dict, target_date: date, start_date: date) -> Optional[int]:
        """Rotating pattern resolution"""
        cycle_length = config.get("cycle_length")
        if not cycle_length or cycle_length <= 0:
            return None
            
        pattern_weeks = config.get("pattern", [])
        if not pattern_weeks:
            return None
        
        # Calculate week in cycle (0-indexed)
        days_diff = (target_date - start_date).days
        cycle_week = (days_diff // 7) % cycle_length
        
        # Get specific week pattern
        week_pattern = next(
            (week for week in pattern_weeks if week.get("week") == cycle_week + 1),
            None
        )
        
        if not week_pattern:
            return None
            
        # Get day in week (0=Monday)
        day_index = target_date.weekday()
        shifts = week_pattern.get("shifts", [])
        
        if day_index < len(shifts):
            shift = shifts[day_index]
            return None if shift == 0 else shift  # 0 represents OFF
        
        return None

    def _resolve_ad_hoc(self, config: dict, target_date: date, user_role: str) -> Optional[int]:
        """Ad-hoc pattern resolution with dynamic rules"""
        # Check dynamic rules first (e.g., event-based conditions)
        for rule in config.get("dynamic_rules", []):
            if self._evaluate_condition(rule.get("condition"), target_date):
                return rule.get("shift")
        
        # Fallback to default shift
        return config.get("fallback_shift")

    def _evaluate_condition(self, condition: str, target_date: date) -> bool:
        """Evaluate dynamic condition (placeholder implementation)"""
        # In production, integrate with your event/forecast system
        if condition == "events > 1000":
            return self._check_event_attendance(target_date) > 1000
        return False

    def _resolve_hybrid(self, config: dict, target_date: date, user_role: str, start_date: date) -> Optional[int]:
        """Hybrid pattern resolution with component priority"""
        for component in config.get("components", []):
            component_type = component.get("type")
            shift = None
            
            if component_type == "ROLE_BASED" and component.get("role") == user_role:
                shift = component.get("shift")
                
            elif component_type == "USER_SPECIFIC" and self.user.id in component.get("user_ids", []):
                shift = component.get("shift")
                
            elif component_type == "ROTATING":
                shift = self._resolve_rotating(component, target_date, start_date)
                
            elif component_type == "AD_HOC":
                shift = self._resolve_ad_hoc(component, target_date, user_role)
            
            if shift is not None:
                return shift
        
        return None
    

class ShiftAssignmentEngine:
    BATCH_SIZE = 500
    
    def __init__(self):
        self.redis = Redis.from_url('redis://localhost:6379/1', decode_responses=True)
    
    async def generate_shifts(self, branch_id: int, start_date: date, end_date: date):
        """Mass assignment optimized for 5M+ RPS"""
        resolver = ShiftResolver(branch_id)
        await resolver.preload_patterns()
        
        # Get active employees in batches
        async for batch in self._get_employee_batches(branch_id):
            assignments = defaultdict(dict)
            
            for date in date_range(start_date, end_date):
                date_key = date.isoformat()
                
                for employee in batch:
                    if shift_id := await resolver.resolve_shift(employee, date):
                        assignments[date_key][employee.id] = shift_id
            
            # Bulk insert with Redis pipeline
            async with self.redis.pipeline() as pipe:
                for date, users in assignments.items():
                    pipe.hset(f"shift_assign:{branch_id}:{date}", mapping=users)
                await pipe.execute()
            
            # Async commit to DB
            await self._bulk_create_assignments(branch_id, assignments)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.redis.aclose()
    
    async def _get_employee_batches(self, branch_id: int):
        """True single-query streaming"""
        branch = await Branch.objects.aget(id=branch_id)
        batch = []
        
        async for user in branch.get_active_users_gen(
            only_fields=['id', 'role'],
            order_by=['id']
        ):
            batch.append(user)
            if len(batch) >= self.BATCH_SIZE:
                yield batch
                batch = []
        
        if batch:
            yield batch
    
    async def _bulk_create_assignments(self, branch_id: int, assignments: dict):
        """Batch insert with conflict handling"""
        objs = [
            StaffShift(
                user_id=user_id,
                shift_id=shift_id,
                date=date,
                branch_id=branch_id
            )
            for date, users in assignments.items()
            for user_id, shift_id in users.items()
        ]
        
        await StaffShift.objects.abulk_create(
            objs,
            update_conflicts=True,
            update_fields=['shift_id'],
            unique_fields=['user_id', 'date']
        )
        
        # Create audit logs
        log_objs = [
            ShiftAssignmentLog(
                branch_id=branch_id,
                user_id=user_id,
                shift_id=shift_id,
                date=date
            )
            for date, users in assignments.items()
            for user_id, shift_id in users.items()
        ]
        await ShiftAssignmentLog.objects.abulk_create(log_objs)

@shared_task(bind=True, max_retries=3)
async def regenerate_shifts(self, branch_id: int, start_date, end_date, priority: int):
    """Regenerate shifts for a branch over a date range."""
    try:
        engine = ShiftAssignmentEngine()
        await engine.generate_shifts(branch_id, start_date, end_date)
    except Exception as exc:
        # Log and retry with exponential backoff
        self.retry(countdown=2 ** self.request.retries, exc=exc)

class ShiftUpdateHandler:
    @classmethod
    async def handle_pattern_change(cls, pattern_id: int):
        """Process pattern updates in real-time"""
        pattern = await ShiftPattern.objects.select_related("branch").aget(id=pattern_id)
        
        # Invalidate cached patterns
        await cache.adelete(f'shift_patterns:{pattern.branch_id}')
        
        # Queue regeneration for affected date range
        end_date = pattern.active_until or (timezone.now().date() + timezone.timedelta(days=14))
        await regenerate_shifts.adelay(
            branch_id=pattern.branch_id,
            start_date=pattern.active_from,
            end_date=end_date,
            priority=1 if pattern.is_temp else 3
        )
    
    @classmethod
    async def handle_emergency_override(cls, user_id: int, branch_id: int, date: date, shift_id: int):
        """Bypass normal resolution for urgent changes"""
        user = await CustomUser.objects.aget(id=user_id)

        # Verify user belongs to branch
        if not await user.branches.filter(id=branch_id).aexists():
            raise ValueError("User not assigned to specified branch")
        
        # Create temporary high-priority pattern
        temp_pattern = await ShiftPattern.objects.acreate(
            user_id=user_id,
            branch_id=user.branch_id,
            pattern_type=ShiftPattern.PatternType.USER_SPECIFIC,
            config={"fixed_schedule": [{"day": date.strftime("%a"), "shift": shift_id}]},
            priority=1000,
            active_from=date,
            active_until=date,
            is_temp=True
        )
        
        # Direct Redis update
        redis_key = f"shift_assign:{user.branch_id}:{date.isoformat()}"
        await cache.ahset(redis_key, str(user_id), shift_id)
        
        # Async DB update
        await StaffShift.objects.aupdate_or_create(
            user_id=user_id,
            date=date,
            defaults={'shift_id': shift_id}
        )

def shiftPatterns(self):

    # Request Data for Each Shift Pattern 
    # 1. Role-Based Configuration 
    {
        "role": "delivery_person",
        "user": null,
        "branch": 1,
        "pattern_type": "RB",
        "config": {
            "exceptions": {
                "days": ["SAT", "SUN"],
                "shift": 1
            },
            "default_shift": 2
        },
        "priority": 1,
        "active_from": "2025-05-16",
        "active_until": "2025-12-31",
        "is_temp": false
    }

    # 2. User-Specific Configuration 
    {
        "role": "cashier",
        "user": 101,
        "branch": 1,
        "pattern_type": "US",
        "config": {
            "fixed_schedule": [
                {"day": "MON", "shift": 1},
                {"day": "WED", "shift": 1},
                {"day": "FRI", "shift": "OFF"}
            ]
        },
        "priority": 2,
        "active_from": "2025-05-16",
        "active_until": "2025-08-31",
        "is_temp": false
    }

    # 3. Rotating Configuration 
    {
        "role": "delivery_person",
        "user": null,
        "branch": 1,
        "pattern_type": "RT",
        "config": {
            "cycle_length": 3,
            "pattern": [
                {"week": 1, "shifts": [1, 1, 1, 1, 0, 0, 0]},
                {"week": 2, "shifts": [2, 2, 2, 2, 0, 0, 0]},
                {"week": 3, "shifts": [0, 0, 0, 0, 0, 0, 0]}
            ]
        },
        "priority": 1,
        "active_from": "2025-05-16",
        "active_until": null,
        "is_temp": false
    }

    # 4. Ad-Hoc Configuration 
    {
        "role": "cashier",
        "user": null,
        "branch": 1,
        "pattern_type": "AH",
        "config": {
            "dynamic_rules": [
                {
                    "condition": "events > 1000",
                    "shift": 4
                }
            ],
            "fallback_shift": 1
        },
        "priority": 3,
        "active_from": "2025-05-16",
        "active_until": "2025-12-31",
        "is_temp": false
    }

    # 5. Hybrid Configuration 
    {
        "role": "cook",
        "user": null,
        "branch": 1,
        "pattern_type": "HY",
        "config": {
            "components": [
                {
                    "type": "ROLE_BASED",
                    "role": "cook",
                    "shift": 2
                },
                {
                    "type": "USER_SPECIFIC",
                    "user_ids": [102],
                    "shift": 1
                },
                {
                    "type": "ROTATING",
                    "role": "delivery_person",
                    "cycle_length": 2,
                    "pattern": [
                        {"week": 1, "shifts": [1, 1, 1, 1, 0, 0, 0]},
                        {"week": 2, "shifts": [2, 2, 2, 2, 0, 0, 0]}
                    ]
                }
            ]
        },
        "priority": 2,
        "active_from": "2025-05-16",
        "active_until": "2025-12-31",
        "is_temp": false
    }