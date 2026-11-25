from django import forms
from django.contrib.auth.forms import UserCreationForm

from hospitals.models import Hospital
from .models import PatientProfile, User


class PatientRegistrationForm(UserCreationForm):
    phone = forms.CharField(max_length=20, help_text="Used to contact you during an emergency")
    emergency_contact_name = forms.CharField(max_length=100, required=False)
    emergency_contact_phone = forms.CharField(max_length=20, required=False)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "phone")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.PATIENT
        user.save()
        PatientProfile.objects.create(
            user=user, emergency_contact_name=self.cleaned_data["emergency_contact_name"],
            emergency_contact_phone=self.cleaned_data["emergency_contact_phone"])
        return user


class ProfessionalRegistrationForm(UserCreationForm):
    phone = forms.CharField(max_length=20)
    hospital = forms.ModelChoiceField(queryset=Hospital.objects.filter(is_active=True))
    professional_id = forms.CharField(max_length=50, label="Licence / staff number")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "phone", "hospital", "professional_id")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.HOSPITAL
        user.is_approved = False  # an administrator must verify the licence first
        user.save()
        return user


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone")


class PatientProfileForm(forms.ModelForm):
    class Meta:
        model = PatientProfile
        fields = ("date_of_birth", "gender", "blood_group", "allergies", "chronic_conditions", "home_address",
                  "emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship",
                  "notify_emergency_contact")
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"}),
                   "allergies": forms.Textarea(attrs={"rows": 2}),
                   "chronic_conditions": forms.Textarea(attrs={"rows": 2})}
        labels = {"notify_emergency_contact": "Send an SMS to my emergency contact when I request help"}
