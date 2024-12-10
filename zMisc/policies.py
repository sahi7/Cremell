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

    def get_role_value(self, request):
        """Helper method to get the numeric value of the user's role."""
        print(f"AccessPolicy invoked for user: {request.user}")
        return request.user.get_role_value()

    def can_create_operational_roles(self, request, view, action):
        """Restrict RestaurantOwner to creating operational roles (>= 5)."""
        role_value = self.get_role_value(request)  # Use helper method to get role value
        return role_value >= 5  # Compare with 5 for operational roles

    def is_in_assigned_country(self, request, view, action):
        """Ensure CountryManager operates within their assigned country."""
        role_value = self.get_role_value(request) 
        if role_value < 3:  # CountryManager's role number is 3
            return False  # Deny access if the user is not a CountryManager
        
        user_country = request.user.country
        target_country = request.data.get("country") or view.get_object().country
        return user_country == target_country

    def is_in_assigned_restaurant(self, request, view, action):
        """Ensure RestaurantManager operates within their assigned restaurant."""
        role_value = self.get_role_value(request)
        if role_value < 4:  # RestaurantManager's role number is 4
            return False  # Deny access if the user is not a RestaurantManager
        
        user_restaurant = request.user.restaurant
        target_restaurant = request.data.get("restaurant") or view.get_object().restaurant
        return user_restaurant == target_restaurant
