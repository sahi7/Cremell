from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model

CustomUser = get_user_model()

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

class RManagerScopePermission(BasePermission):
    """
    Ensures that the specified manager for a restaurant belongs to the correct scope.
    """
    def has_permission(self, request, view):
        if view.action in ['create', 'update', 'partial_update']:
            self._check_manager_for_restaurant(request)
        return True

    def _check_manager_for_restaurant(self, request):
        manager_id = request.data.get('manager')
        company_id = request.data.get('company')

        if manager_id:
            try:
                manager = CustomUser.objects.get(id=manager_id)
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("The specified manager does not exist."))

            # Check if the manager belongs to the RestaurantManager group
            if not manager.groups.filter(name="RestaurantManager").exists():
                raise PermissionDenied(_("The manager must be a restaurant manager."))

            # If company_id is provided, validate the manager belongs to the company
            if company_id:
                if not manager.companies.filter(id=company_id).exists():
                    raise PermissionDenied(_("The manager must belong to the specified company."))

            # For standalone restaurants
            else:
                if manager.created_by != request.user:
                    raise PermissionDenied(
                        _("For standalone restaurants, the manager must be created by the owner.")
                    )
                if manager.companies.exists():
                    raise PermissionDenied(
                        _("The manager cannot belong to any company.")
                    )

class BManagerScopePermission(BasePermission):
    """
    Ensures that the specified manager for a branch belongs to the correct scope.
    """
    def has_permission(self, request, view):
        if view.action in ['create', 'update', 'partial_update']:
            self._check_manager_for_branch(request)
        return True

    def _check_manager_for_branch(self, request):
        manager_id = request.data.get('manager')

        if manager_id:
            try:
                manager = CustomUser.objects.get(id=manager_id)
            except CustomUser.DoesNotExist:
                raise PermissionDenied(_("The specified manager does not exist."))

            # Check if the manager belongs to the BranchManager group
            if not manager.groups.filter(name="BranchManager").exists():
                raise PermissionDenied(_("The manager must belong to the BranchManager group."))


class ObjectStatusPermission(BasePermission):
    """
    Ensures that actions are allowed only on objects with status "active".
    """
    def has_object_permission(self, request, view, obj):
        """
        Check the status of the restaurant or branch.
        Deny any actions on inactive objects.
        """
        if obj.status != 'active':
            # Only (CompanyAdmin, CountryManager) can modify the status
            if not request.user.groups.filter(name__in=["CompanyAdmin", "CountryManager"]).exists():
                raise PermissionDenied(_("This object is inactive and cannot be modified."))
        return True
