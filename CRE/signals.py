from redis.asyncio import Redis
from django.conf import settings
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate, post_save, post_delete
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import StaffShift, Shift
from .models import Order, Menu, MenuCategory, MenuItem
from notifications.models import EmployeeTransfer

CustomUser = get_user_model()
# Define your model groupings
GLOBAL_MODELS = ["customuser", "company"]  # Models managed at a global level
SCOPED_MODELS = ["restaurant"]  # Models managed at a restaurant level
BRANCH_MODELS = ["order", "branch"] # Models managed at a branch level

# Define group permissions with specific model access
GROUP_PERMISSIONS = {
    "CompanyAdmin": {"models": GLOBAL_MODELS + SCOPED_MODELS + BRANCH_MODELS, "actions": ["add", "change", "delete", "view"]},
    "RestaurantOwner": {"models": GLOBAL_MODELS + SCOPED_MODELS + BRANCH_MODELS, "actions": ["add", "change", "delete", "view"]},
    "CountryManager": {"models": SCOPED_MODELS + BRANCH_MODELS, "actions": ["add", "view", "change"]},
    "RestaurantManager": {"models": SCOPED_MODELS + BRANCH_MODELS, "actions": ["add", "change", "view"]},
    "BranchManager": {"models": BRANCH_MODELS, "actions": ["add", "change", "view"]},
}

# Exclude apps that should not be considered for permission assignment
EXCLUDED_APPS = ["auth", "contenttypes", "sessions", "admin"]


@receiver(post_migrate)
def create_groups_and_manage_permissions(sender, **kwargs):
    """
    Signal to dynamically create groups, assign permissions, and clean up obsolete groups or permissions.
    """
    # Iterate over all installed apps
    for app_config in apps.get_app_configs():
        if app_config.name in EXCLUDED_APPS:
            continue  # Skip excluded apps
        
        # Loop through models in the current app
        for model in app_config.get_models():
            model_name = model._meta.model_name
            
            # Determine if the model is part of GLOBAL or SCOPED models
            if model_name in GLOBAL_MODELS + SCOPED_MODELS + BRANCH_MODELS:
                for group_name, data in GROUP_PERMISSIONS.items():
                    if model_name in data["models"]:
                        # Create or get the group
                        group, created = Group.objects.get_or_create(name=group_name)

                        # Assign the permissions for the model to the group
                        for action in data["actions"]:
                            # Generate the permission codename
                            codename = f"{action}_{model_name}"
                            # Get or create the permission
                            permission, perm_created = Permission.objects.get_or_create(
                                codename=codename,
                                content_type__app_label=app_config.name,
                                content_type__model=model_name,
                                defaults={"name": f"Can {action} {model_name}"}
                            )
                            # Add permission to the group
                            group.permissions.add(permission)
    
    # Cleanup unused permissions and groups
    cleanup_unused_permissions_and_groups()


def cleanup_unused_permissions_and_groups():
    """
    Remove permissions and groups that no longer have corresponding models or are not defined in GROUP_PERMISSIONS.
    """
    # Collect defined group names and model permissions
    defined_groups = set(GROUP_PERMISSIONS.keys())
    defined_permissions = set()

    for group_name, data in GROUP_PERMISSIONS.items():
        for model_name in data["models"]:
            for action in data["actions"]:
                defined_permissions.add(f"{action}_{model_name}")

    # Remove obsolete groups
    for group in Group.objects.all():
        if group.name not in defined_groups:
            group.delete()

    # Remove obsolete permissions
    for permission in Permission.objects.all():
        if permission.codename not in defined_permissions:
            permission.delete()

# @receiver(post_save, sender=CustomUser)
# def invalidate_user_cache(sender, instance, **kwargs):
#     redis_client = Redis.from_url('redis://localhost:6379')
#     redis_client.delete(f'user_data_{instance.id}_*')
#     redis_client.delete(f'stakeholders_*')


@receiver(post_save, sender=Order)
async def handle_order_creation(sender, instance, created, **kwargs):
    if created:
        # Create initial task
        from notifications.tasks import create_initial_task
        create_initial_task.delay(instance.id)

@receiver(post_save, sender=StaffShift)
async def update_availability_on_shift_change(sender, instance, **kwargs):
    """Update StaffAvailability when a StaffShift is created or modified (e.g., overtime added)."""
    if hasattr(instance.user, 'availability'):
        await instance.user.availability.update_status()

@receiver(post_save, sender='cre.OvertimeRequest')
def notify_manager_on_overtime_request(sender, instance, created, **kwargs):
    """
    Trigger WebSocket notification when an OvertimeRequest is created.
    Handled in WebSocket layer below.
    """
    if created:
        pass  # WebSocket notification logic implemented in consumers

@receiver(post_save, sender=Shift)
@receiver(post_delete, sender=Shift)
async def invalidate_shift_cache(sender, instance, **kwargs):
    cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    branch_id = instance.branch_id
    cache_key = f"shift_ids:branch_{branch_id}"
    await cache.delete(cache_key)

@receiver(post_save, sender=EmployeeTransfer)
@receiver(post_delete, sender=EmployeeTransfer)
async def invalidate_user_role_id(sender, instance, **kwargs):
    cache = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    user_id = instance.user_id
    # cache_key = f"role_id:user_{user_id}:{instance.user.role}"
    # await cache.delete(cache_key)

@receiver([post_save, post_delete], sender=Menu)
@receiver([post_save, post_delete], sender=MenuCategory)
@receiver([post_save, post_delete], sender=MenuItem)
async def invalidate_menu_cache(sender, instance, **kwargs):
    async with Redis.from_url(settings.REDIS_URL, decode_responses=True) as redis:
        branch_id = None
        if sender == Menu:
            branch_id = instance.branch_id
        elif sender == MenuCategory:
            branch_id = instance.menu.branch_id
        elif sender == MenuItem:
            instance_with_relations = await MenuItem.objects.select_related(
                'category__menu'
            ).aget(pk=instance.pk)
            branch_id = instance_with_relations.category.menu.branch_id
        
        if branch_id:
            await redis.delete(f"pos_menu{branch_id}")