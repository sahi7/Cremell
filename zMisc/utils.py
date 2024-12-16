from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from django.utils.translation import gettext as _

def check_user_role(user, max_role_value=4):
    """
    Check if a user's role value is within the allowed range.
    Raises a PermissionDenied exception if not.

    Args:
        user (CustomUser): The user object to check.
        max_role_value (int): The maximum allowed role value.

    Returns:
        None: If the user is authorized.
    """
    role_value = user.get_role_value()
    if role_value is None or role_value > max_role_value:
        raise PermissionDenied(_("You are not authorized to perform this action."))

def validate_scope(user, data, allowed_scopes):
    """
    Validates if the request data falls within the allowed scope of the user.
    
    Args:
        user (CustomUser): The request user.
        data (dict): The data being validated (e.g., request.data).
        allowed_scopes (dict): A dictionary defining allowed fields and their values.
        
    Returns:
        None: If validation passes.
        
    Raises:
        serializers.ValidationError: If validation fails.
    """
    for field, scope in allowed_scopes.items():
        if field in data and data[field] not in scope:
            raise ValidationError({
                field: _("You cannot create objects outside your assigned {}.").format(field)
            })

