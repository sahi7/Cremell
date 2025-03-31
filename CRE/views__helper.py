from django.utils.translation import gettext_lazy as _

from rest_framework.response import Response
# from rest_framework.views import APIView
from rest_framework import status

from adrf.views import APIView
from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied
from django.db import models
from django.contrib.auth import get_user_model

from .models import Branch, Restaurant, Company, Country 
from .serializers import RestaurantSerializer, CompanySerializer, CountrySerializer, AssignmentSerializer
from zMisc.policies import ScopeAccessPolicy

CustomUser = get_user_model()

class CheckUserExistsView(APIView):
    """
    API View to check if a user exists based on email or phone number.
    """

    def get(self, request, *args, **kwargs):
        email = request.query_params.get('email')
        phone_number = request.query_params.get('phone_number')

        if not email and not phone_number:
            return Response(
                {"detail": _("Please provide either 'email' or 'phone_number' as a query parameter.")},
                status=status.HTTP_400_BAD_REQUEST
            )

        user_exists = CustomUser.objects.filter(
            email=email if email else None,
            phone_number=phone_number if phone_number else None
        ).exists()

        return Response({"user_exists": user_exists}, status=status.HTTP_200_OK)

class UserScopeView(APIView):
    """
    API view to return the current user's associated restaurants, companies, and countries.
    """

    def get(self, request, *args, **kwargs):
        user = request.user

        # Get the user's associated data
        restaurants = user.restaurants.all()
        companies = user.companies.all()
        countries = user.countries.all()

        # Serialize the data
        restaurant_data = RestaurantSerializer(restaurants, many=True).data
        company_data = CompanySerializer(companies, many=True).data
        country_data = CountrySerializer(countries, many=True).data

        # Return the combined response
        return Response({
            "restaurants": restaurant_data,
            "companies": company_data,
            "countries": country_data,
        })
    
class AssignmentView(APIView):
    """
    Method: PATCH
    Assign User to a Branch:
        {
            "user_id": 5,
            "object_type": "branch",
            "object_id": 3,
            "field_name": "manager"
        }
    Set User Status:
        {
            "user_id": 5,
            "object_type": "user",
            "status": "active"
        }
    Set Branch Status:
        {
            "object_type": "branch",
            "object_id": 3,
            "status": "open"
        }
    """
    serializer_class = AssignmentSerializer
    permission_classes = [ScopeAccessPolicy]

    async def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        data = serializer.validated_data

        object_type = data['object_type']
        new_status = data.get('status')

        if object_type == 'user':
            if 'user_id' not in data:
                raise PermissionDenied(_("user_id required for user operations"))
            user = await sync_to_async(CustomUser.objects.get)(id=data['user_id'])
            if new_status:
                await self._handle_status_change(request, user, new_status)
            else:
                raise PermissionDenied(_("Status required for user updates"))
        else:
            if 'object_id' not in data:
                raise PermissionDenied(_("object_id required for object operations"))
            model_map = {'restaurant': Restaurant, 'branch': Branch}
            model = model_map.get(object_type)
            if not model:
                raise PermissionDenied(_("Invalid object type"))
            obj = await sync_to_async(model.objects.get)(id=data['object_id'])

            if 'user_id' in data:
                user = await sync_to_async(CustomUser.objects.get)(id=data['user_id'])
                field_name = data.get('field_name')
                await self._handle_object_assignment(request, user, obj, field_name, model)
            elif new_status:
                await self._handle_object_status_change(request, obj, new_status, model)
            else:
                raise PermissionDenied(_("Specify a user assignment or status change"))

        return Response({"message": _("Updated {object_type} successfully").format(object_type=object_type)}, 
                        status=status.HTTP_200_OK)

    async def _handle_status_change(self, request, user, new_status):
        allowed_scopes = await ScopeAccessPolicy().get_allowed_scopes(request, self, self.action)
        
        # Flexible scope check: try companies, then restaurants, then branches
        user_scope_ids = set()
        if hasattr(user, 'companies'):
            user_scope_ids = await sync_to_async(lambda: set(user.companies.values_list('id', flat=True)))()
        elif hasattr(user, 'restaurants'):
            user_scope_ids = await sync_to_async(lambda: set(user.restaurants.values_list('id', flat=True)))()
        elif hasattr(user, 'branches'):
            user_scope_ids = await sync_to_async(lambda: set(user.branches.values_list('id', flat=True)))()

        # Check against allowed scopes (companies, restaurants, or branches)
        if user_scope_ids:
            if ('companies' in allowed_scopes and not user_scope_ids.issubset(allowed_scopes['companies'])) and \
               ('restaurants' in allowed_scopes and not user_scope_ids.issubset(allowed_scopes['restaurants'])) and \
               ('branches' in allowed_scopes and not user_scope_ids.issubset(allowed_scopes['branches'])):
                raise PermissionDenied(_("User not in your scope"))

        # Update status (no is_active fallback)
        if not hasattr(user, 'status'):
            raise PermissionDenied(_("User model has no status field"))
        await sync_to_async(setattr)(user, 'status', new_status)
        await sync_to_async(user.save)()
        await self._send_notifications(user, 'user', user.id, f"status to {new_status}")

    async def _handle_object_assignment(self, request, user, obj, field_name, model):
        if field_name not in model._meta.fields_map or not isinstance(model._meta.get_field(field_name), models.ForeignKey):
            raise PermissionDenied(_("Invalid assignment field: {field_name}").format(field_name=field_name))
        await sync_to_async(setattr)(obj, field_name, user)
        await sync_to_async(obj.save)()
        await self._send_notifications(user, model.__name__.lower(), obj.id, field_name)

    async def _handle_object_status_change(self, request, obj, new_status, model):
        if 'status' not in model._meta.fields:
            raise PermissionDenied(_("{model_name} has no status field").format(model_name=model.__name__))
        await sync_to_async(setattr)(obj, 'status', new_status)
        await sync_to_async(obj.save)()
        user_to_notify = getattr(obj, 'manager', None) or request.user
        if user_to_notify:
            await self._send_notifications(user_to_notify, model.__name__.lower(), obj.id, f"status to {new_status}")

    async def _send_notifications(self, user, object_type, object_id, field_name):
        from .tasks import send_assignment_email
        send_assignment_email.delay(user.id, object_type, object_id, field_name)
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{user.id}",
            {
                'type': 'assignment_notification',
                'message': _("Updated {object_type} ID {object_id} with {field_name}").format(
                    object_type=object_type, object_id=object_id, field_name=field_name
                ),
            }
        )