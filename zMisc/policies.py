from rest_access_policy import AccessPolicy

class UserAccessPolicy(AccessPolicy):
    statements = [
        # Allow CompanyAdmin to perform all actions
        {
            "action": "*",
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
        },
        # Restrict RestaurantOwner actions
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": ["is_in_assigned_restaurant", "target_has_lower_role_value"],
        },
        # Restrict CountryManager actions
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
            "condition": ["is_in_assigned_country", "target_has_lower_role_value"],
        },
    ]

    def target_has_lower_role_value(self, request, view, action):
        """Check if the target user being acted upon has a lower role value."""
        if action in ["create"]:
            # For creation, check if the role value in the request payload is lower
            target_role = request.data.get("role")
            if target_role:
                target_role_value = request.user.get_role_value(target_role)
                return target_role_value < request.user.get_role_value()
        elif action in ["update", "delete", "retrieve"]:
            # For update/delete/retrieve, get the target user from the queryset
            target_user = view.get_object()
            return target_user.get_role_value() < request.user.get_role_value()
        return False  # Deny by default

    def is_in_assigned_restaurant(self, request, view, action):
        """Ensure RestaurantOwner is limited to their assigned restaurant(s)."""
        if request.user.role == "restaurant_owner":
            if action in ["list"]:
                return True  # Handled in queryset filtering
            target_user = view.get_object()
            return target_user.restaurants.filter(id__in=request.user.restaurants.values("id")).exists()
        return False

    def is_in_assigned_country(self, request, view, action):
        """Ensure CountryManager is limited to their assigned country."""
        if request.user.role == "country_manager":
            if action in ["list"]:
                return True  # Handled in queryset filtering
            target_user = view.get_object()
            return target_user.country == request.user.country
        return False
