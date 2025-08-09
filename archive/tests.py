from django.test import TestCase
from django.apps import apps
from .utils import get_object_graph

class ObjectGraphTest(TestCase):
    def setUp(self):
        # Get the model dynamically
        model = apps.get_model('cre', 'Branch')
        print("Model:", model)

        # Create an instance for testing
        self.instance = model.objects.create(pk=23, name="Test Branch")  # Adjust fields as necessary

    def test_object_graph(self):
        # Use self.instance instead of self.model
        object_graph = get_object_graph(self.instance.__class__, self.instance)
        print("Object Graph:", object_graph)