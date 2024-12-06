from django.contrib.auth.models import Group, Permission
from django.apps import apps

GLOBAL_MODELS = ["user", "branch", "region"]  # Models managed at a global level
SCOPED_MODELS = ["menu", "order", "inventory"]  # Models managed at a branch level

GROUP_PERMISSIONS = {
    "CompanyAdmin": {"models": GLOBAL_MODELS + SCOPED_MODELS, "actions": ["add", "change", "delete", "view"]},
    "CountryManager": {"models": SCOPED_MODELS, "actions": ["view", "change"]},
}

for group_name, data in GROUP_PERMISSIONS.items():
    group, created = Group.objects.get_or_create(name=group_name)
    for model_name in data["models"]:
        model = apps.get_model(app_label="CRE", model_name=model_name)
        for action in data["actions"]:
            codename = f"{action}_{model._meta.model_name}"
            try:
                permission = Permission.objects.get(codename=codename)
                group.permissions.add(permission)
            except Permission.DoesNotExist:
                print(f"Permission {codename} not found.")
