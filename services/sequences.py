# services/order_numbers.py
import uuid
import asyncio
import redis.asyncio as redis
from typing import Optional
from django.utils import timezone
from django.conf import settings
from datetime import timedelta
from CRE.models import Branch, SequenceCounter

redis_client = redis.from_url(settings.REDIS_URL)

class SequencePool:
    def __init__(self, branch_id: int, sequence_type: str, max_digits: int = 4):
        self.branch_id = branch_id
        self.sequence_type = sequence_type
        self.max_digits = max_digits
        self.max_value = 10 ** max_digits - 1  # e.g., 9999 for 4 digits
        self.pool_key = f"seq_pool:{branch_id}:{sequence_type}"
        self.counter_key = f"seq_counter:{branch_id}:{sequence_type}"

    async def pregenerate_batch(self, batch_size: int = 100):
        """Atomically generates a batch of sequence numbers"""
        async with await redis_client.pipeline() as pipe:
            # Get current counter value
            current = await pipe.get(self.counter_key)
            if current is None:
                current = 0
            current = int(current)
            
            # Generate batch
            sequences = []
            for i in range(1, batch_size + 1):
                next_val = current + i
                if next_val > self.max_value:
                    next_val = 1  # Rollover
                sequences.append(f"{next_val:0{self.max_digits}d}")

            # Update counter and store batch
            await pipe.set(self.counter_key, next_val)
            await pipe.lpush(self.pool_key, *sequences)
            await pipe.execute()

    async def get_next(self, prefix: str = "", suffix: str = "") -> Optional[str]:
        """Retrieves a pregenerated sequence with formatting"""
        seq = await redis_client.rpop(self.pool_key)
        if seq is None:
            return None
            
        return f"{prefix}{seq}{suffix}"

    async def ensure_capacity(self, min_available: int = 50):
        """Ensures minimum available sequences"""
        available = await redis_client.llen(self.pool_key)
        if available < min_available:
            await self.pregenerate_batch()


async def generate_order_number(branch: "Branch") -> str:
    """
    Format: [CountryCode][RollingSequence]-[Random6]
    Example: US0042-A3B9F2 (4-digit sequence)
    """
    pool = SequencePool(
        branch_id=branch.id,
        sequence_type="order",
        max_digits=4  # Configurable per branch
    )
    
    # Get next pregenerated sequence
    country_code = await branch.acountry_code
    if (number := await pool.get_next(
        prefix=country_code,
        suffix=f"-{uuid.uuid4().hex[:6].upper()}"
    )) is None:
        # Emergency fallback (should rarely trigger)
        await pool.pregenerate_batch(10)
        number = await pool.get_next(
            prefix=country_code,
            suffix=f"-{uuid.uuid4().hex[:6].upper()}"
        )
    
    # Async replenishment check
    asyncio.create_task(pool.ensure_capacity())
    return number