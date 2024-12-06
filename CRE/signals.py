from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.db.models.signals import post_migrate
from django.dispatch import receiver

# Define your model groupings
GLOBAL_MODELS = ["customuser", "company"]  # Models managed at a global level
SCOPED_MODELS = ["restaurant", "order", "branch"]  # Models managed at a branch level

# Define group permissions with specific model access
GROUP_PERMISSIONS = {
    "CompanyAdmin": {"models": GLOBAL_MODELS + SCOPED_MODELS, "actions": ["add", "change", "delete", "view"]},
    "CountryManager": {"models": SCOPED_MODELS, "actions": ["view", "change"]},
    "RestaurantManager": {"models": SCOPED_MODELS, "actions": ["view"]},
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
            if model_name in GLOBAL_MODELS + SCOPED_MODELS:
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
