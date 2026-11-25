from django.urls import path

from . import views

urlpatterns = [
    path("", views.assistant_page, name="assistant"),
    path("ask/", views.ask, name="assistant_ask"),
]
