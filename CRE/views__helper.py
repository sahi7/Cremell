from django.utils.translation import gettext_lazy as _

from rest_framework.response import Response
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
from zMisc.permissions import EntityUpdatePermission, ObjectStatusPermission
# from zMisc.utils import log_activity

import logging

logger = logging.getLogger(__name__)

CustomUser = get_user_model()

class CheckUserExistsView(APIView):
    """
    API View to check if a user exists based on email or phone number.
    GET: check-user/?email=mail@gmail.com
    """

    async def get(self, request, *args, **kwargs):
        email = request.query_params.get('email')
        phone_number = request.query_params.get('phone_number') # without '+'


        if not email and not phone_number:
            return Response(
                {"detail": _("Please provide either 'email' or 'phone_number' as a query parameter.")},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Build filter kwargs dynamically
        filter_kwargs = {}
        if email:
            filter_kwargs['email'] = email
        if phone_number:
            filter_kwargs['phone_number'] = phone_number

        user_exists = await CustomUser.objects.filter(**filter_kwargs).aexists()

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
    Method: PATCH assignments/
    Rule: Add Policy requires for scope
    Assign User to a Branch:
        Sets Branch(id=3).manager = CustomUser(id=5)
        {
            "user_id": 5,
            "object_type": "branch",
            "object_id": 3,
            "field_name": "manager"
        }
    Set Object Status:
        Sets CustomUser(id=5).email = "newemail@example.com"
        {
            "object_type": "user",
            "object_id": 5,
            "field_name": "email",
            "field_value": "newemail@example.com"
        }
        Sets Branch(id=3).name = "Downtown Branch"
        {
            "object_type": "branch",
            "object_id": 3,
            "field_name": "name",
            "field_value": "Downtown Branch"
        }
    Remove Manager:
        {
            "object_type": "branch", 
            "object_id": 3, 
            "field_name": "manager", 
            "action": "remove"
        }

    """
    serializer_class = AssignmentSerializer
    permission_classes = (ScopeAccessPolicy, EntityUpdatePermission, )

    async def patch(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        data = serializer.validated_data

        object_type = data['object_type']
        object_id = data['object_id']
        field_name = data['field_name']
        user_id = data.get('user_id')
        field_value = data.get('field_value')

        # Use MODEL_MAP from permission class
        model = EntityUpdatePermission.MODEL_MAP.get(object_type)
        obj = await model.objects.aget(id=object_id)

        # Validate field (minimal check since permission already ensures existence)
        if not field_value:
            try:
                field = model._meta.get_field(field_name)
                if not isinstance(field, models.ForeignKey):
                    raise PermissionDenied(_("{model_name} field '{field_name}' is not a ForeignKey").format(model_name=model.__name__, field_name=field_name))
            except Exception as e:
                # Catch any other unexpected exceptions and deny permission
                raise PermissionDenied(_("An unexpected error occurred "))
                # raise PermissionDenied(_("An unexpected error occurred while checking permissions for {model_name}: {error}").format(
                #     model_name=model.__name__, error=str(e)
                # ))

        old_manager = await sync_to_async(getattr)(obj, field_name, None)

        # Handle update (permissions already checked)
        if data.get('action') == "remove":
            await self._handle_removal(obj, field_name, model, data, old_manager)
        elif user_id is not None:
            user = await sync_to_async(CustomUser.objects.get)(id=user_id)  # consider using user from permission to avoid extra db hit
            await self._handle_user_assignment(obj, user, field_name, model, data, old_manager)
        elif field_value is not None:
            await self._handle_field_update(obj, field_name, field_value, model, request)

        return Response({"message": _("Updated {object_type} successfully").format(object_type=object_type)}, 
                        status=status.HTTP_200_OK)

    
    async def _update_user_m2m(self, user, obj, model, action='add', old_user=None):
        """Reusable helper to manage ManyToManyField updates."""
        USER_FIELD_MAP = {
            'restaurant': 'restaurants',
            'branch': 'branches',
            'user': 'employees',
        }
        object_type = model.__name__.lower()
        user_field = USER_FIELD_MAP.get(object_type)

        if not user_field:
            return  # No M2M field for this object type

        try:
            if not isinstance(CustomUser._meta.get_field(user_field), models.ManyToManyField):
                raise PermissionDenied(_("{field_name} on UserModel is not a ManyToManyField").format(field_name=user_field))
            
            m2m_manager = getattr(user, user_field)
            if action == 'add':
                await sync_to_async(m2m_manager.add)(obj)
                if old_user and old_user != user:
                    await sync_to_async(getattr(old_user, user_field).remove)(obj)
            elif action == 'remove' and user:
                await sync_to_async(m2m_manager.remove)(obj)
        except models.FieldDoesNotExist:
            raise PermissionDenied(_("{field_name} on UserModel is not a ManyToManyField").format(field_name=user_field))

    
    async def _handle_user_assignment(self, obj, user, field_name, model, data, old_manager):
        
        if user == old_manager:
            raise PermissionDenied(_("Already active"))

        if old_manager and not data.get('force_update') == "True":
            raise PermissionDenied(_("{object_type} already has a {field_name}. Use 'force': true to overwrite").format(object_type=data['object_type'], field_name=data['field_name']))

        await sync_to_async(setattr)(obj, field_name, user)
        await sync_to_async(obj.save)()

       # Update M2M
        await self._update_user_m2m(user, obj, model, 'add', old_manager)
        
        # Track History: Log activity with constructed details
        details = {
            'field_name': field_name,
            'new_manager': user.id,
        }
        if old_manager:
            details['old_manager'] = old_manager.id
        activity_type = 'manager_assign' if not old_manager else 'manager_replace'
        
        from .tasks import log_activity
        log_activity.delay(self.request.user.id, activity_type, details, obj.id, data['object_type'])
        
        # Notify: Inform both managers
        object_type = model.__name__.lower()
        await self._send_notifications(user, object_type, obj.id, f"assigned as {field_name}")
        if old_manager and old_manager != user:
            await self._send_notifications(old_manager, object_type, obj.id, f"removed as {field_name}")

    async def _handle_field_update(self, obj, field_name, field_value, model, request=None):
        if field_name == 'status':
            if obj.status == field_value:
                raise PermissionDenied(_("Already set"))
            await ObjectStatusPermission().check_parent_status(obj, field_name, field_value, request)
        # Validate field
        if field_name not in [f.name for f in model._meta.fields]:
            raise PermissionDenied(_("{model_name} has no field '{field_name}'").format(model_name=model.__name__, field_name=field_name))
        
        field_obj = model._meta.get_field(field_name)
        old_value = await sync_to_async(getattr)(obj, field_name)

        if field_value is not None:
            if field_obj.get_internal_type() in ['IntegerField', 'BigIntegerField']:
                field_value = int(field_value)
            elif field_obj.get_internal_type() == 'BooleanField':
                field_value = bool(field_value.lower() == 'true' if isinstance(field_value, str) else field_value)

        await sync_to_async(setattr)(obj, field_name, field_value)
        await sync_to_async(obj.save)()

        # Track History: Log field update
        details = {
            'field_name': field_name,
            'old_value': str(old_value) if old_value is not None else None,
            'new_value': str(field_value) if field_value is not None else None,
        }

        from .tasks import log_activity
        log_activity.delay(self.request.user.id, 'field_update', details, obj.id)

        user_to_notify = await sync_to_async(getattr)(obj, 'manager', None) or (obj if model == CustomUser else self.request.user)
        if user_to_notify:
            await self._send_notifications(user_to_notify, model.__name__.lower(), obj.id, f"{field_name} to {field_value}")

    async def _handle_removal(self, obj, field_name, model, data, old_manager):

        if not old_manager:
            raise PermissionDenied(_("{object_type} has no {field_name} to remove").format(object_type=data['object_type'], field_name=field_name))

        # Clear the field
        await sync_to_async(setattr)(obj, field_name, None)
        await sync_to_async(obj.save)()

        # Update M2M
        await self._update_user_m2m(old_manager, obj, model, 'remove')

        # Track History
        details = {'field_name': field_name, 'old_manager': old_manager.id}

        from .tasks import log_activity
        log_activity.delay(self.request.user.id, 'manager_remove', details, obj.id, data['object_type'])

        # Notify
        object_type = model.__name__.lower()
        await self._send_notifications(old_manager, object_type, obj.id, f"removed as {field_name}")

    async def _send_notifications(self, user, object_type, object_id, field_update):
        from .tasks import send_assignment_email
        send_assignment_email.delay(user.id, object_type, object_id, field_update)
        # channel_layer = get_channel_layer()
        # await channel_layer.group_send(
        #     f"user_{user.id}",
        #     {
        #         'type': 'update_notification',
        #         'message': _("Updated {object_type} ID {object_id} with {field_update}").format(
        #             object_type=object_type, object_id=object_id, field_update=field_update
        #         ),
        #     }
        # )