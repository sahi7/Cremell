import json
from django.apps import apps
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from channels.layers import get_channel_layer
from celery import shared_task

from .utils import get_object_graph
from .models import DeletedObject
import logging

CustomUser = get_user_model()
logger = logging.getLogger(__name__)

@shared_task(bind=True)
def finalize_deletion(self, object_type, object_id):
    try:
        # Check if this deletion was reverted
        deleted_obj = DeletedObject.objects.filter(
            object_type=object_type,
            object_id=object_id,
            status='pending'
        ).first()
        
        if not deleted_obj:
            logger.info(f"Deletion was already reverted for {object_type} {object_id}")
            return

        # Proceed with finalization
        model = apps.get_model(object_type)
        obj = model.objects.get(pk=object_id)
        
        with transaction.atomic():
            obj.is_active = False
            obj.save()
            
            deleted_obj.status = 'deleted'
            deleted_obj.save()
            
    except Exception as e:
        logger.error(f"Finalization failed for {object_type} {object_id}: {str(e)}")
        self.retry(exc=e, countdown=60)


@shared_task
def send_email_notification(user_id, message):
    """Send email to critical stakeholders."""
    logger.info(f"Sending email to user {user_id}: {message}")
    # Implement email logic

@shared_task
def handle_deletion_tasks(object_type, object_id, user_id, cleanup_task_id):
    """Handle DeletedObject creation and notifications."""
    try:
        model = apps.get_model(object_type)
        # obj = model.objects.get(pk=object_id)
        finalize = timezone.now() + timezone.timedelta(hours=48)

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            ValueError(f"No user matching ID for: {user_id}")


        # Get complete object graph
        object_graph = get_object_graph(model, object_id)
        
        # Create DeletedObject
        # DeletedObject will help to keep track of objects that have been deleted until we can move them to another database
        deleted_obj = DeletedObject(
            object_type=object_type,
            object_id=object_id,
            object_graph=json.dumps(object_graph),
            grace_period_expiry=finalize,
            deleted_by=user,
            cleanup_task_id=cleanup_task_id
        )
        deleted_obj.save()
        logger.info(f"Created DeletedObject for {object_type} {object_id}")

        # Notify stakeholders via Channels
        channel_layer = get_channel_layer()
        group_name = f"{object_type.lower()}_{object_id}_staff"
        channel_layer.group_send(
            group_name,
            {
                "type": "deletion.update",
                "object_type": object_type,
                "object_id": object_id,
                "message": f"{object_type} {object_id} marked inactive, revert by {finalize}.",
                "grace_period_expiry": finalize.isoformat()
            }
        )

        # Notify managers via email
        # managers = CustomUser.objects.filter(role__in=['restau_owner', 'regional_admin']).values_list('id', flat=True)
        # for manager_id in managers:
        #     send_email_notification.delay(
        #         manager_id,
        #         f"{object_type} {object_id} marked inactive by user {user_id}, revert by {finalize}."
        #     )
        logger.info(f"Notified stakeholders for {object_type} {object_id}")
    except Exception as e:
        logger.error(f"Error in handle_deletion_tasks for {object_type} {object_id}: {str(e)}")
        raise


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