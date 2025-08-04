from adrf.views import APIView
from rest_framework import status
from rest_framework.response import Response
from asgiref.sync import sync_to_async
from django.contrib.auth.models import Permission
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import gettext_lazy as _
from .models import Branch, CustomUser, BranchPermissionPool, BranchPermissionAssignment
from .serializers import BranchPermissionAssignmentSerializer, BranchPermissionPoolSerializer
from .tasks import create_permission_assignments, update_permission_pool
from .permissions import BranchPermissionAss
from CRE.tasks import log_activity
from zMisc.utils import clean_request_data
from zMisc.policies import ScopeAccessPolicy

class BranchPermissionAssignmentView(APIView):
    # Role rank mapping for validation
    ROLE_RANKS = {
        'company_admin': 1,
        'restaurant_owner': 2,
        'country_manager': 3,
        'restaurant_manager': 4,
        'branch_manager': 5,
        'shift_leader': 6,
        'cashier': 7,
        'cook': 8,
        'food_runner': 9,
        'cleaner': 10,
        'delivery_man': 11,
        'utility_worker': 12,
    }
    permission_classes = [ScopeAccessPolicy, BranchPermissionAss]

    async def post(self, request, *args, **kwargs):
        """
        Assign permissions:
        {
            "user_ids": [1, 2],
            "branch_id": 123,
            "permission_ids": [101, 102, 103],
            "start_time": "2025-08-03T00:00:00Z",
            "end_time": "2025-08-10T23:59:59Z",
            "conditions": {}
        }
        """
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = request.branch.id
        serializer = BranchPermissionAssignmentSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        user_ids = data.get('user_ids', [])
        role_names = data.get('role_names', [])
        branch_id = data['branch_id']
        permission_ids = data['permission_ids']
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        conditions = data.get('conditions', {})
        assigned_by = request.user

        # # Validate branch_manager role (rank 5)
        # if not assigned_by.role or self.ROLE_RANKS.get(assigned_by.role) != 5:
        #     return Response(
        #         {"error": _("Only branch managers can assign permissions.")},
        #         status=status.HTTP_403_FORBIDDEN
        #     )

        try:
            # Async fetch branch and permission pool
            branch = request.branch
            permission_pool = await sync_to_async(BranchPermissionPool.objects.get)(branch=branch)

            # Validate permissions exist in the pool
            pool_permission_ids = await sync_to_async(
                lambda: list(permission_pool.permissions.values_list('id', flat=True))
            )()
            invalid_permissions = [pid for pid in permission_ids if pid not in pool_permission_ids]
            if invalid_permissions:
                return Response(
                    {"error": _("Permissions %s not in branch permission pool.") % invalid_permissions},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Prepare data for Celery task
            assignments = []
            log_details = {
                'start_time': start_time.isoformat() if start_time else None,
                'end_time': end_time.isoformat() if end_time else None,
                'conditions': conditions,
                'permission_ids': permission_ids
            }

            if user_ids:
                # Validate users are associated with the branch
                users = await sync_to_async(
                    lambda: list(CustomUser.objects.filter(id__in=user_ids, branches=branch))
                )()
                if len(users) != len(user_ids):
                    invalid_users = set(user_ids) - set(user.id for user in users)
                    return Response(
                        {"error": _("Users %s not associated with branch %s.") % (invalid_users, branch_id)},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Prepare assignments for users
                for user in users:
                    for permission_id in permission_ids:
                        assignments.append({
                            'user_id': user.id,
                            'branch_id': branch_id,
                            'permission_id': permission_id,
                            'start_time': start_time,
                            'end_time': end_time,
                            'conditions': conditions,
                            'assigned_by_id': assigned_by.id
                        })

            if role_names:
                # Validate roles have users associated with the branch
                role_users = await sync_to_async(
                    lambda: list(CustomUser.objects.filter(role__in=role_names, branches=branch))
                )()
                valid_roles = {user.role for user in role_users}
                invalid_roles = [role for role in role_names if role not in valid_roles]
                if invalid_roles:
                    return Response(
                        {"error": _("No users with roles %s in branch %s.") % (invalid_roles, branch_id)},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Prepare assignments for roles
                for role in role_names:
                    for permission_id in permission_ids:
                        assignments.append({
                            'role': role,
                            'branch_id': branch_id,
                            'permission_id': permission_id,
                            'start_time': start_time,
                            'end_time': end_time,
                            'conditions': conditions,
                            'assigned_by_id': assigned_by.id
                        })

            # Offload to Celery
            create_permission_assignments.delay(assignments, assigned_by.id, branch_id)

            return Response(
                {"message": _("Permission assignment task queued successfully.")},
                status=status.HTTP_202_ACCEPTED
            )

        except ObjectDoesNotExist:
            return Response(
                {"error": _("Permission pool not found.")},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    async def delete(self, request, *args, **kwargs):
        """
        Delete permissions
        {
            "user_ids": [1, 2],
            "branch_id": 123,
            "permission_ids": [101, 102]
        }
        """
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = int(request.data['branches'][0])
        serializer = BranchPermissionAssignmentSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        user_ids = data.get('user_ids', [])
        role_names = data.get('role_names', [])
        branch_id = data['branch_id']
        permission_ids = data['permission_ids']
        assigned_by = request.user

        # # Validate branch_manager role (rank 5)
        # if not assigned_by.role or self.ROLE_RANKS.get(assigned_by.role) != 5:
        #     return Response(
        #         {"error": _("Only branch managers can delete permissions.")},
        #         status=status.HTTP_403_FORBIDDEN
        #     )

        try:
            branch = await sync_to_async(Branch.objects.get)(id=branch_id)
            filters = {'branch': branch, 'permission_id__in': permission_ids}
            if user_ids:
                filters['user_id__in'] = user_ids
            elif role_names:
                filters['role__in'] = role_names
            else:
                return Response(
                    {"error": _("Either user_ids or role_names must be provided.")},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Delete assignments and log
            assignments = await sync_to_async(
                lambda: list(BranchPermissionAssignment.objects.filter(**filters))
            )()
            if not assignments:
                return Response(
                    {"error": _("No matching permission assignments found.")},
                    status=status.HTTP_404_NOT_FOUND
                )

            log_details = {'permission_ids': permission_ids}
            for assignment in assignments:
                if assignment.user:
                    log_details['target_user_id'] = assignment.user.id
                else:
                    log_details['target_role'] = assignment.role
                await sync_to_async(log_activity.delay)(
                    assigned_by.id, 'delete_permission', log_details, branch.id, 'branch'
                )

            await sync_to_async(BranchPermissionAssignment.objects.filter(**filters).delete)()

            return Response(
                {"message": _("Permissions deleted successfully.")},
                status=status.HTTP_200_OK
            )

        except ObjectDoesNotExist:
            return Response(
                {"error": _("Branch not found.")},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class BranchPermissionPoolView(APIView):
    # Role rank mapping for validation
    ROLE_RANKS = {
        'company_admin': 1,
        'restaurant_owner': 2,
        'country_manager': 3,
        'restaurant_manager': 4,
        'branch_manager': 5,
        'shift_leader': 6,
        'cashier': 7,
        'cook': 8,
        'food_runner': 9,
        'cleaner': 10,
        'delivery_man': 11,
        'utility_worker': 12,
    }
    permission_classes = [ScopeAccessPolicy, BranchPermissionAss]

    async def post(self, request, *args, **kwargs):
        """
        Create/update permission pool
        {
            "branch_id": 123,
            "permission_ids": [101, 102, 103]
        }
        """
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = request.branch.id
        serializer = BranchPermissionPoolSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        branch_id = data['branch_id']
        permission_ids = data['permission_ids']
        created_by = request.user

        # # Validate user role (rank 1–4)
        # if not created_by.role or self.ROLE_RANKS.get(created_by.role) not in [1, 2, 3, 4]:
        #     return Response(
        #         {"error": _("Only company_admin, restaurant_owner, country_manager, or restaurant_manager can manage permission pools.")},
        #         status=status.HTTP_403_FORBIDDEN
        #     )

        try:
            # Validate permissions exist
            existing_permission_ids = await sync_to_async(
                lambda: list(Permission.objects.filter(id__in=permission_ids).values_list('id', flat=True))
            )()
            print("existing_permission_ids: ", existing_permission_ids)
            invalid_permissions = [pid for pid in permission_ids if pid not in existing_permission_ids]
            if invalid_permissions:
                return Response(
                    {"error": _("Invalid permission IDs: %s.") % invalid_permissions},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Offload to Celery
            update_permission_pool.delay(branch_id, permission_ids, created_by.id)

            return Response(
                {"message": _("Permission pool update task queued successfully.")},
                status=status.HTTP_202_ACCEPTED
            )

        except ObjectDoesNotExist:
            return Response(
                {"error": _("Branch not found.")},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    async def get(self, request, branch_id, *args, **kwargs):
        # # Validate user role (rank 1–4)
        # if not request.user.role or self.ROLE_RANKS.get(request.user.role) not in [1, 2, 3, 4]:
        #     return Response(
        #         {"error": _("Only company_admin, restaurant_owner, country_manager, or restaurant_manager can view permission pools.")},
        #         status=status.HTTP_403_FORBIDDEN
        #     )

        try:
            # Async fetch permission pool
            pool = await sync_to_async(BranchPermissionPool.objects.get)(branch_id=branch_id)
            permission_ids = await sync_to_async(
                lambda: list(pool.permissions.values_list('id', flat=True))
            )()

            return Response(
                {
                    "branch_id": branch_id,
                    "permission_ids": permission_ids,
                    "created_by": pool.created_by.username if pool.created_by else None,
                    "created_at": pool.created_at,
                    "updated_at": pool.updated_at
                },
                status=status.HTTP_200_OK
            )

        except ObjectDoesNotExist:
            return Response(
                {"error": _("Permission pool not found for branch.")},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    async def delete(self, request, branch_id, *args, **kwargs):
        cleaned_data = clean_request_data(request.data)
        data = cleaned_data
        data['branch_id'] = int(request.data['branches'][0])
        serializer = BranchPermissionPoolSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        permission_ids = data['permission_ids']
        created_by = request.user

        # Validate user role (rank 1–4)
        if not created_by.role or self.ROLE_RANKS.get(created_by.role) not in [1, 2, 3, 4]:
            return Response(
                {"error": _("Only company_admin, restaurant_owner, country_manager, or restaurant_manager can manage permission pools.")},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            # Async fetch branch and pool
            branch = await sync_to_async(Branch.objects.get)(id=branch_id)
            pool = await sync_to_async(BranchPermissionPool.objects.get)(branch=branch)

            # Validate permissions exist in pool
            pool_permission_ids = await sync_to_async(
                lambda: list(pool.permissions.values_list('id', flat=True))
            )()
            invalid_permissions = [pid for pid in permission_ids if pid not in pool_permission_ids]
            if invalid_permissions:
                return Response(
                    {"error": _("Permissions %s not in branch permission pool.") % invalid_permissions},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Remove permissions and log
            await sync_to_async(pool.permissions.remove)(*permission_ids)
            log_details = {'permission_ids': permission_ids, 'action': 'remove'}
            await sync_to_async(log_activity.delay)(
                created_by.id, 'update_pool', log_details, branch.id, 'branch'
            )

            return Response(
                {"message": _("Permissions removed from pool successfully.")},
                status=status.HTTP_200_OK
            )

        except ObjectDoesNotExist:
            return Response(
                {"error": _("Branch or permission pool not found.")},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )