import json
from django.apps import apps
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from celery import shared_task

from .utils import get_object_graph
from .models import DeletedObject, ObjectHistory
from notifications.tasks import send_batch_notifications
import logging

CustomUser = get_user_model()
logger = logging.getLogger(__name__)

@shared_task(bind=True)
def finalize_deletion(self, object_type, object_id, user_id):
    try:
        # Check if this deletion was reverted
        deleted_obj = DeletedObject.objects.filter(
            object_type=object_type,
            object_id=object_id,
            status='pending_deletion'
        ).first()
        
        if not deleted_obj:
            logger.info(f"Deletion was already reverted for {object_type} {object_id}")
            return False

        # Proceed with finalization
        model = apps.get_model(object_type)
        obj = model.objects.get(pk=object_id)
        
        with transaction.atomic():
            obj.is_active = False
            obj.save()
            
            deleted_obj.status = 'deleted'
            deleted_obj.save()

            ObjectHistory.objects.create(
                object_type=object_type,
                object_id=object_id,
                action='deleted',
                user_id=user_id,
                details='Deletion permanently removed'
            )
            
        return True
            
    except Exception as e:
        logger.error(f"Finalization failed for {object_type} {object_id}: {str(e)}")
        self.retry(exc=e, countdown=60)


@shared_task
def send_email_notification(user_id, message):
    """Send email to critical stakeholders."""
    logger.info(f"Sending email to user {user_id}: {message}")
    # Implement email logic

@shared_task
def handle_deletion_tasks(object_type, object_id, user_id, cleanup_task_id, finalize):
    """Handle DeletedObject creation and notifications."""
    try:
        model = apps.get_model(object_type)
        obj = model.objects.get(pk=object_id)

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            ValueError(f"No user matching ID for: {user_id}")


        # Get complete object graph
        object_map = get_object_graph(object_type, object_id)
        
        # Create DeletedObject
        # DeletedObject will help to keep track of objects that have been deleted until we can move them to another database
        deleted_obj = DeletedObject(
            object_type=object_type,
            object_id=object_id,
            object_map=object_map,
            grace_period_expiry=finalize,
            deleted_by=user,
            cleanup_task_id=cleanup_task_id
        )
        deleted_obj.save()
        logger.info(f"Created DeletedObject for {object_type} {object_id}")

        # Notify stakeholders via Channels
        channel_layer = get_channel_layer()
        group_name = f"{object_type.lower()}_{object_id}_staff"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "deletion.update",
                "object_type": object_type,
                "object_id": object_id,
                "message": f"{object_type} {object_id} marked inactive, revert by {finalize}.",
                "grace_period_expiry": finalize.isoformat()
            }
        )
        app_label, model_name = object_type.split('.')

        # Prepare notification details
        message = f"The {model_name} '{obj.name}' has been marked for deletion."
        subject = f"{model_name} Deletion Notification"
        extra_context = {
                        'object_type': object_type,
                        'object_name': obj.name,
                        'finalize': finalize,
                        'initiator_name': f"{user.first_name} {user.last_name}" ,
                        'initiator_role': user.role
                    }
        # Determine scope for stakeholders
        company_id = None
        restaurant_id = None
        branch_id = None
        country_id = None

        if model_name == 'Branch':
            branch_id = object_id
            restaurant_id = obj.restaurant_id
            # Handle both company-owned and standalone restaurants
            company_id = getattr(obj.restaurant, 'company_id', None)
        elif model_name == 'Restaurant':
            restaurant_id = object_id
            # Handle standalone restaurants (company_id may be None)
            company_id = getattr(obj, 'company_id', None)
        elif model_name == 'Company':
            company_id = object_id
        elif model_name == 'Country':
            country_id = object_id
        # Send notifications to stakeholders
        send_batch_notifications.delay(
            company_id=company_id,
            restaurant_id=restaurant_id,
            branch_id=branch_id,
            country_id=country_id,
            message=message,
            subject=subject,
            # max_role_value=12,  # All roles 
            # include_lower_roles=True,
            extra_context=extra_context
        )
        logger.info(f"Notified stakeholders for {object_type} {object_id}")
        return True
    except Exception as e:
        logger.error(f"Error in handle_deletion_tasks for {object_type} {object_id}: {str(e)}")
        raise

@shared_task(bind=True)
def revert_deletion_task(self, object_type, object_id, user_id):
    """
    Celery task to revert deletion of an object
    Args:
        object_type (str): Model name in 'app.Model' format
        object_id (int): ID of the object to revert
        user_id (int): ID of the user performing the revert
    stateDiagram-v2
        [*] --> Active
        Active --> Inactive: User deletes
        Inactive --> Active: User reverts
        Inactive --> Deleted: Grace period expires
        Deleted --> [*]
    """
    try:
        with transaction.atomic():
            from celery import current_app 
            model = apps.get_model(object_type)
            
            # Lock the deletion record
            deleted_obj = DeletedObject.objects.select_for_update().filter(
                object_type=object_type,
                object_id=object_id,
                status='pending_deletion'
            ).order_by('-deleted_on').first()
            
            # Revoke cleanup task
            # Safe task revocation
            if deleted_obj.cleanup_task_id:
                try:
                    current_app.control.revoke(
                        deleted_obj.cleanup_task_id,
                        terminate=True,
                        signal='SIGTERM'
                    )
                except Exception as e:
                    logger.warning(f"Failed to revoke task {deleted_obj.cleanup_task_id}: {str(e)}")
            
            # Restore object
            obj = model.objects.get(pk=object_id)
            if not obj.is_active:
                obj.is_active = True
                obj.save()
            
            # Update deletion record
            deleted_obj.status = 'reverted'
            deleted_obj.reverted_by_id = user_id
            deleted_obj.reverted_at = timezone.now()
            deleted_obj.save()
            
            return True
            
    except DeletedObject.DoesNotExist:
        logger.warning(f"No pending deletion for {object_type} {object_id}")
        return False
    except Exception as e:
        logger.error(f"Revert failed: {str(e)}")
        # self.retry(exc=e, countdown=60, max_retries=3)


@shared_task
def migrate_object_graph(object_graph, user_id, source_db, target_db):
    from django.db import connections
    
    try:
        # Get main object
        main_model_str = object_graph['main_object']['model']
        main_id = object_graph['main_object']['id']
        model = apps.get_model(main_model_str)
        
        # 1. Migrate all dependencies first (bottom-up)
        for model_str, ids in object_graph['dependencies'].items():
            dep_model = apps.get_model(model_str)
            
            # Get all objects from source DB
            with connections[source_db].cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {dep_model._meta.db_table} WHERE id IN %s",
                    [tuple(ids)]
                )
                rows = cursor.fetchall()
            
            # Insert into target DB
            with connections[target_db].cursor() as cursor:
                for row in rows:
                    columns = [col.name for col in dep_model._meta.fields]
                    placeholders = ', '.join(['%s'] * len(columns))
                    cursor.execute(
                        f"INSERT INTO {dep_model._meta.db_table} ({', '.join(columns)}) VALUES ({placeholders})",
                        row
                    )
        
        # 2. Migrate main object
        with connections[source_db].cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {model._meta.db_table} WHERE id = %s",
                [main_id]
            )
            row = cursor.fetchone()
        
        with connections[target_db].cursor() as cursor:
            columns = [col.name for col in model._meta.fields]
            placeholders = ', '.join(['%s'] * len(columns))
            cursor.execute(
                f"INSERT INTO {target_db}.{model._meta.db_table} ({', '.join(columns)}) VALUES ({placeholders})",
                row
            )
            
        logger.info(f"Successfully migrated {main_model_str} ID {main_id} and dependencies")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        raise