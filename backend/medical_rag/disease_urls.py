from django.urls import path

from . import disease_views as v

urlpatterns = [
    path("", v.disease_list, name="disease_list"),
    path("new/", v.disease_edit, name="disease_new"),
    path("<slug:slug>/", v.disease_detail, name="disease_detail"),
    path("<slug:slug>/edit/", v.disease_edit, name="disease_edit"),
    path("<slug:slug>/review/", v.disease_review, name="disease_review"),
]
