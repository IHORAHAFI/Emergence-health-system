from django.urls import path

from . import views

urlpatterns = [
    path("", views.assistant_page, name="assistant"),
    path("ask/", views.ask, name="assistant_ask"),
    path("history/", views.history, name="assistant_history"),
    path("history/<int:pk>/", views.history, name="assistant_history_item"),
]
