from rest_access_policy import AccessPolicy

class UserAccessPolicy(AccessPolicy):
    statements = [
        # Rule 1: Allow CompanyAdmin to perform all actions
        {
            "action": "*",
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
        },
        # Rule 2: Allow RestaurantOwner to create roles >= 5 (e.g., operational roles)
        {
            "action": ["create"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": "can_create_operational_roles",
        },
        # Rule 3: Allow CountryManager to manage users in their country
        {
            "action": ["list", "retrieve", "update", "delete"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
            "condition": "is_in_assigned_country",
        },
        # Rule 4: Allow RestaurantManager to manage users in their restaurant
        {
            "action": ["list", "retrieve", "update", "delete"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
            "condition": "is_in_assigned_restaurant",
        },
        # Default Rule: Deny everything else
        {
            "action": "*",
            "principal": "*",
            "effect": "deny",
        },
    ]

    def can_create_operational_roles(self, request, view, action):
        """Restrict RestaurantOwner to creating operational roles (>= 5)."""
        role = request.data.get("role")
        return role and int(role) >= 5

    def is_in_assigned_country(self, request, view, action):
        """Ensure CountryManager operates within their assigned country."""
        user_country = request.user.country
        target_country = request.data.get("country") or view.get_object().country
        return user_country == target_country

    def is_in_assigned_restaurant(self, request, view, action):
        """Ensure RestaurantManager operates within their assigned restaurant."""
        user_restaurant = request.user.restaurant
        target_restaurant = request.data.get("restaurant") or view.get_object().restaurant
        return user_restaurant == target_restaurant
