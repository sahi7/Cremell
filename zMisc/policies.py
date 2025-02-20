from rest_access_policy import AccessPolicy
from CRE.models import Branch, Restaurant

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
        {
            "action": ["list", "retrieve"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
        },
    ]

class RestaurantAccessPolicy(AccessPolicy):
    statements = [
        {
            "action": ["list", "retrieve", "branches", "employees", "company"],
            "principal": ["group:CompanyAdmin", "group:RestaurantOwner", "group:CountryManager"],
            "effect": "allow",
        },
        {
            "action": ["create"],
            "principal": ["group:CompanyAdmin", "group:CountryManager"],
            "effect": "allow",
        },
        {
            "action": ["update", "partial_update"],
            "principal": ["group:RestaurantOwner", "group:CompanyAdmin"],
            "effect": "allow",
        },
        {
            "action": ["partial_update"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
        },
        {
            "action": ["destroy"],
            "principal": ["group:CompanyAdmin", "group:RestaurantOwner"],
            "effect": "allow",
        },
    ]


class BranchAccessPolicy(AccessPolicy):
    statements = [
        # Restaurant Owner: Full access to manage their restaurant's branches, including creation
        {
            "action": ["list", "retrieve", "create", "update", "partial_update", "destroy"],
            "principal": ["group:RestaurantOwner"],
            "effect": "allow",
            "condition": "is_owner_of_restaurant",
        },
        # Restaurant Manager: Limited access to their branches, including creation if they belong to a company
        {
            "action": ["list", "retrieve", "create", "update", "partial_update"],
            "principal": ["group:RestaurantManager"],
            "effect": "allow",
            "condition": "is_manager_of_restaurant",
        },
        # Country Manager: Can view all branches in their country
        {
            "action": ["list", "retrieve", "create", "update"],
            "principal": ["group:CountryManager"],
            "effect": "allow",
            "condition": "is_in_country_manager_country",
        },
        # Company Admin: Full access to all branches
        {
            "action": ["list", "retrieve", "create", "update", "partial_update", "destroy"],
            "principal": ["group:CompanyAdmin"],
            "effect": "allow",
        },
        # Branch Manager: Can manage only the branch they are assigned to
        {
            "action": ["list", "retrieve", "update", "partial_update"],
            "principal": ["group:BranchManager"],
            "effect": "allow",
            "condition": "is_manager_of_branch",
        },
    ]

    def is_owner_of_restaurant(self, request, view, action):
        """
        Check if the branch is linked to a restaurant created by the current user (for create, update, delete).
        Also, check if the restaurant is active before allowing branch creation.
        """
        print(f'Action: {action}') 
        if action in ["create", "update", "partial_update", "destroy"]:
            # For creating a branch, check if the restaurant belongs to the current user.
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, created_by=request.user, status='active').exists()
        elif action in ["list", "retrieve"]:
            return True
        return False  # For non-create actions, allow if already satisfied

    def is_manager_of_restaurant(self, request, view, action):
        """
        Check if the branch belongs to a restaurant managed by the current user (for update, delete).
        Also, allow branch creation if the user belongs to a company.
        """
        if action in ["update", "partial_update", "destroy"]:
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, manager=request.user).exists()
        elif action in ["list", "retrieve"]:
            print('yesfff')
            restaurant_id = view.kwargs.get('pk')
            if restaurant_id:
                return Restaurant.objects.filter(id=restaurant_id, created_by=request.user).exists()
        elif action == "create":
            # For branch creation, check if the user belongs to a company.
            return request.user.companies.exists()
        return False

    def is_in_country_manager_country(self, request, view, action):
        """
        Check if the branch is located within the country managed by the current user.
        """
        if action in ["list", "retrieve"]:
            restaurant = view.request.data.get('restaurant')
            if restaurant:
                return Restaurant.objects.filter(id=restaurant, country__in=request.user.countries.all()).exists()
        return False

    def is_manager_of_branch(self, request, view, action):
        """
        Check if the branch is managed by the current user (BranchManager).
        BranchManagers can only view and manage the branch they are assigned to.
        """
        if action in ["list", "retrieve", "update", "partial_update"]:
            # For list/retrieve, ensure the user can only view their branch.
            branch_id = view.kwargs.get('pk')  # Assuming the branch ID is passed in the URL
            if branch_id:
                return Branch.objects.filter(id=branch_id, manager=request.user).exists()
        return False


