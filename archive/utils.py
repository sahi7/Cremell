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
    # Initialize the graph structure
    graph = {
        'main_object': {
            'model': target_model,  # e.g. 'cre.Branch'
            'id': target_id
        },
        'dependencies': defaultdict(list)
    }
    
    # Split target_model into app_label and model_name
    app_label, model_name = target_model.split('.')
    
    # Get the actual model class
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        raise ValueError(f"Model {target_model} not found")
    
    # Find all dependent models and their related objects
    for related_model in apps.get_models():
        for field in related_model._meta.get_fields():
            # Check all relation types that could point to our target model
            if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
                if field.related_model == model:
                    # Get the related objects
                    related_objects = field.model.objects.filter(**{field.name: target_id})
                    
                    # Add to dependencies
                    model_key = f"{related_model._meta.app_label}.{related_model.__name__}"
                    graph['dependencies'][model_key].extend(
                        obj.id for obj in related_objects
                    )
    
    # Convert defaultdict to regular dict for JSON serialization
    graph['dependencies'] = dict(graph['dependencies'])
    return graph
