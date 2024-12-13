from rest_framework.exceptions import PermissionDenied
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

def get_user_scope(user):
    """
    Determines the scope (companies, restaurants, countries) based on the user's role.
    """
    if user.groups.filter(name="CompanyAdmin").exists():
        # CompanyAdmin: Can view all users in their company
        return user.companies.all(), user.restaurants.none(), user.countries.none()
    
    elif user.groups.filter(name="RestaurantOwner").exists():
        # RestaurantOwner: Can view users associated with their restaurants
        return user.companies.all(), user.restaurants.all(), user.countries.none()
    
    elif user.groups.filter(name="CountryManager").exists():
        # CountryManager: Can view users in their country and company
        return user.companies.all(), user.restaurants.none(), user.countries.all()
    
    elif user.groups.filter(name="RestaurantManager").exists():
        # RestaurantManager: Can view users for restaurants they manage
        return user.companies.all(), user.restaurants.all(), user.countries.none()

    # Default case if no matching group found
    return user.companies.none(), user.restaurants.none(), user.countries.none()

