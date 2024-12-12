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
        },
        # Restrict CountryManager actions
        {
            "action": ["list", "retrieve", "create", "update", "delete"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
        },
    ]

