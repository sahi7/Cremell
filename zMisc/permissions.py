from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

class UserCreationPermission(BasePermission):
    """
    Ensure that new users can only be associated with the companies, countries,
    restaurants, and branches that the creating user is associated with.
    """
    
    def has_permission(self, request, view):
        # Allow other actions (e.g., GET) without checks
        if view.action != "create":
            return True
        
        # Only proceed if creating a user
        user = request.user

        # Extract related object IDs from the request data
        requested_companies = request.data.get("companies", [])
        requested_countries = request.data.get("countries", [])
        requested_restaurants = request.data.get("restaurants", [])

        # Validate each relationship
        if requested_companies:
            if not self._is_subset(user.companies.values_list('id', flat=True), requested_companies):
                raise PermissionDenied(_("You can only assign companies you are associated with."))

        if requested_countries:
            if not self._is_subset(user.countries.values_list('id', flat=True), requested_countries):
                raise PermissionDenied(_("You can only assign countries you are associated with."))

        if requested_restaurants:
            if not self._is_subset(user.restaurants.values_list('id', flat=True), requested_restaurants):
                raise PermissionDenied(_("You can only assign restaurants you are associated with."))
        
        return True

    @staticmethod
    def _is_subset(user_objects, requested_objects):
        """
        Check if all requested_objects are in user_objects.
        """
        return set(requested_objects).issubset(set(user_objects))

class ManagerScopePermission(BasePermission):
    """
    Ensures that the specified manager belongs to the requesting user's scope.
    """
    def has_permission(self, request, view):
        # Apply permission checks only for create, update, and partial_update actions
        if view.action in ['create', 'update', 'partial_update']:
            manager_id = request.data.get('manager')
            if manager_id:
                try:
                    manager = CustomUser.objects.get(id=manager_id)
                except CustomUser.DoesNotExist:
                    raise PermissionDenied(_("The specified manager does not exist."))

                # Check if the manager is part of the RestaurantManager group
                if not manager.groups.filter(name="RestaurantManager").exists():
                    raise PermissionDenied(_("The manager must belong to the RestaurantManager group."))

                # Retrieve company or standalone context
                company_id = request.data.get('company')

                # Validation for standalone restaurants
                if company_id is None:  # Standalone
                    if manager.created_by != request.user:
                        raise PermissionDenied(
                            _("For standalone restaurants, the manager must be created by the requesting user.")
                        )
                    if manager.companies.exists():
                        raise PermissionDenied(
                            _("The manager cannot belong to any company for standalone restaurants.")
                        )

                # Validation for company restaurants
                else:  # Company-owned
                    if not manager.companies.filter(id=company_id).exists():
                        raise PermissionDenied(
                            _("The manager must belong to the specified company for company-owned restaurants.")
                        )

        return True
