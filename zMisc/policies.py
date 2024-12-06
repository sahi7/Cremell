from rest_access_policy import AccessPolicy

class UserAccessPolicy(AccessPolicy):
    statements = [
        # Company Admin Permissions
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
        },
        # Restaurant Owner Permissions
        {
            "action": ["create", "list"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": "can_manage_restaurant_users"
        },
        # Country Manager Permissions
        {
            "action": ["create", "list", "retrieve"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
            "condition": "is_in_country"
        },
        # Restaurant Manager Permissions
        {
            "action": ["list", "retrieve"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
            "condition": "is_restaurant_manager"
        },
        # Deny All Other Actions
        {
            "action": "*",
            "principal": "*",
            "effect": "deny"
        }
    ]

    def can_manage_restaurant_users(self, request, view, action) -> bool:
        # RestaurantOwner can create/manage roles ≥ 5
        return request.user.role == 2 and action == "create"

    def is_in_country(self, request, view, action) -> bool:
        # Ensure CountryManager is working within their country
        user_country = request.user.country
        requested_country = request.data.get("country")
        return user_country == requested_country

    def is_restaurant_manager(self, request, view, action) -> bool:
        # Ensure the user is tied to a restaurant they manage
        return request.user.role == 4 and request.user.restaurant == request.query_params.get("restaurant")
