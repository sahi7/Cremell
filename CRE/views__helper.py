from django.utils.translation import gettext_lazy as _

from rest_framework.response import Response
# from rest_framework.views import APIView
from rest_framework import status

from adrf.views import APIView
from adrf.viewsets import ModelViewSet
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
    permission_classes = (ScopeAccessPolicy, )

    async def patch(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        data = serializer.validated_data

        object_type = data['object_type']
        object_id = data['object_id']
        field_name = data['field_name']
        user_id = data.get('user_id')  # Optional, for assignments
        field_value = data.get('field_value')  # Optional, for direct updates

        # Map object types to models
        model_map = {
            'user': CustomUser,
            'branch': Branch,
            'restaurant': Restaurant,
        }
        model = model_map.get(object_type)
        if not model:
            raise PermissionDenied(_("Invalid object type"))

        # Fetch the object
        obj = await sync_to_async(model.objects.get)(id=object_id)

        # Handle update based on presence of user_id or field_value
        if user_id is not None:
            user = await sync_to_async(CustomUser.objects.get)(id=user_id)
            await self._handle_user_assignment(request, obj, user, field_name, model)
        elif field_value is not None:
            await self._handle_field_update(request, obj, field_name, field_value, model)
        else:
            raise PermissionDenied(_("Specify either user_id for assignment or field_value for update"))

        return Response({"message": _("Updated {object_type} successfully").format(object_type=object_type)}, 
                        status=status.HTTP_200_OK)

    async def _handle_user_assignment(self, request, obj, user, field_name, model):
        # Get allowed scopes from policy
        allowed_scopes = await ScopeAccessPolicy().get_allowed_scopes(request, self, self.action)

        # Check object scope
        obj_scope_ids = await self._get_object_scope_ids(obj, model)
        if obj_scope_ids and not any(obj_scope_ids.issubset(allowed_scopes.get(scope, set())) for scope in ['companies', 'restaurants', 'branches']):
            raise PermissionDenied(_("Object not in your scope"))

        # Check user scope
        user_scope_ids = await self._get_object_scope_ids(user, CustomUser)
        if user_scope_ids and not any(user_scope_ids.issubset(allowed_scopes.get(scope, set())) for scope in ['companies', 'restaurants', 'branches']):
            raise PermissionDenied(_("User not in your scope"))

        # Validate and assign user to field
        if field_name not in model._meta.fields_map or not isinstance(model._meta.get_field(field_name), models.ForeignKey):
            raise PermissionDenied(_("{model_name} has no ForeignKey field '{field_name}'").format(model_name=model.__name__, field_name=field_name))
        await sync_to_async(setattr)(obj, field_name, user)
        await sync_to_async(obj.save)()

        # Notify
        await self._send_notifications(user, model.__name__.lower(), obj.id, f"assigned as {field_name}")

    async def _handle_field_update(self, request, obj, field_name, field_value, model):
        # Get allowed scopes from policy
        allowed_scopes = await ScopeAccessPolicy().get_allowed_scopes(request, self, self.action)

        # Check object scope
        obj_scope_ids = await self._get_object_scope_ids(obj, model)
        if obj_scope_ids and not any(obj_scope_ids.issubset(allowed_scopes.get(scope, set())) for scope in ['companies', 'restaurants', 'branches']):
            raise PermissionDenied(_("Object not in your scope"))

        # Validate and update field
        if field_name not in [f.name for f in model._meta.fields]:
            raise PermissionDenied(_("{model_name} has no field '{field_name}'").format(model_name=model.__name__, field_name=field_name))
        
        # Convert field_value to the correct type
        field_obj = model._meta.get_field(field_name)
        if field_value is not None:
            if field_obj.get_internal_type() in ['IntegerField', 'BigIntegerField']:
                field_value = int(field_value)
            elif field_obj.get_internal_type() == 'BooleanField':
                field_value = bool(field_value.lower() == 'true' if isinstance(field_value, str) else field_value)

        await sync_to_async(setattr)(obj, field_name, field_value)
        await sync_to_async(obj.save)()

        # Notify
        user_to_notify = getattr(obj, 'manager', None) or (obj if model == CustomUser else request.user)
        if user_to_notify:
            await self._send_notifications(user_to_notify, model.__name__.lower(), obj.id, f"{field_name} to {field_value}")

    async def _get_object_scope_ids(self, obj, model):
        """Extract scope-relevant IDs from the object."""
        if model == CustomUser:
            if hasattr(obj, 'companies'):
                return await sync_to_async(lambda: set(obj.companies.values_list('id', flat=True)))()
            elif hasattr(obj, 'restaurants'):
                return await sync_to_async(lambda: set(obj.restaurants.values_list('id', flat=True)))()
            elif hasattr(obj, 'branches'):
                return await sync_to_async(lambda: set(obj.branches.values_list('id', flat=True)))()
        elif model == Branch:
            return {obj.company_id} if obj.company_id else set()
        elif model == Restaurant:
            return {obj.company_id} if obj.company_id else set()
        return set()

    async def _send_notifications(self, user, object_type, object_id, field_update):
        from .tasks import send_assignment_email
        send_assignment_email.delay(user.id, object_type, object_id, field_update)
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            f"user_{user.id}",
            {
                'type': 'update_notification',
                'message': _("Updated {object_type} ID {object_id} with {field_update}").format(
                    object_type=object_type, object_id=object_id, field_update=field_update
                ),
            }
        )