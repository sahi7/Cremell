import json
from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.db.models import ForeignKey, ManyToManyField, OneToOneField
from collections import defaultdict
from celery.app.control import Control

from .models import DeletedObject, ObjectHistory
import logging

logger = logging.getLogger(__name__)
def get_object_graph(target_model, target_id):
    """
    Returns complete dependency graph for an object in JSON-serializable format
    Format: {
        'main_object': {'model': 'app.Model', 'id': 1},
        'dependencies': {
            'app.Model1': [1, 2, 3],
            'app.Model2': [4, 5],
            ...
        }
    }
    """
    graph = {
        'main_object': {
            'model': f'{target_model._meta.app_label}.{target_model.__name__}',
            # 'model': f'{target_model}',
            'id': target_id
        },
        'dependencies': defaultdict(list)
    }
    
    # Track processed objects to avoid cycles
    processed = set()
    
    def traverse(model, obj_id):
        key = f"{model._meta.app_label}.{model.__name__}|{obj_id}"
        if key in processed:
            return
        processed.add(key)
        
        # Get the actual instance
        obj = model.objects.get(pk=obj_id)
        
        # Check all relation types
        for field in model._meta.get_fields():
            if isinstance(field, (ForeignKey, OneToOneField)):
                related_obj = getattr(obj, field.name)
                if related_obj:
                    related_model = field.related_model
                    graph['dependencies'][f"{related_model._meta.app_label}.{related_model.__name__}"].append(related_obj.id)
                    traverse(related_model, related_obj.id)
            
            elif isinstance(field, ManyToManyField):
                related_manager = getattr(obj, field.name)
                for related_obj in related_manager.all():
                    related_model = field.related_model
                    graph['dependencies'][f"{related_model._meta.app_label}.{related_model.__name__}"].append(related_obj.id)
                    traverse(related_model, related_obj.id)
    
    traverse(target_model, target_id)
    
    # Convert defaultdict to regular dict for JSON serialization
    graph['dependencies'] = dict(graph['dependencies'])
    print(graph)
    return graph

async def revert_deletion(object_type, object_id, user_id):
    """
    Cancel pending deletion before grace period expires
    stateDiagram-v2
        [*] --> Active
        Active --> Inactive: User deletes
        Inactive --> Active: User reverts
        Inactive --> Deleted: Grace period expires
        Deleted --> [*]
    """
    try:
        model = apps.get_model(object_type)
        async with transaction.atomic():
            deleted_obj = await DeletedObject.objects.select_for_update().aget(
                object_type=object_type,
                object_id=object_id,
                status='pending_deletion'
            )
            
            # Revoke the scheduled task
            Control().revoke(deleted_obj.cleanup_task_id)
            
            # Restore the original object
            obj = await model.objects.aget(pk=object_id)
            if not obj.is_active:
                obj.is_active = True
                await obj.asave()
                        
            # Mark as reverted
            deleted_obj.status = 'reverted'
            deleted_obj.reverted_by = user_id
            deleted_obj.reverted_at = timezone.now()
            await deleted_obj.asave()


            
            return True
            
    except DeletedObject.DoesNotExist:
        logger.warning(f"No pending deletion found for {object_type} ID {object_id}")
        return False