from django import forms

from .models import EmergencyRequest


class EmergencyRequestForm(forms.Form):
    # patient information (pre-filled from the profile; editable when calling for someone else)
    patient_name = forms.CharField(max_length=120, required=False, label="Full name of the person who needs help")
    gender = forms.ChoiceField(required=False, choices=[("", "Not specified"), ("female", "Female"),
                                                        ("male", "Male"), ("other", "Other")])
    symptoms = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, max_length=1000,
                               label="Signs and symptoms", help_text="e.g. vomiting, high fever, can't move left arm")
    patient_condition = forms.ChoiceField(choices=EmergencyRequest.Condition.choices, initial="unknown",
                                          required=False, label="Patient condition right now")
    seizure = forms.BooleanField(required=False, label="Seizure / convulsions")
    head_injury = forms.BooleanField(required=False, label="Head injury")
    request_ambulance = forms.BooleanField(required=False, initial=True, label="Send an ambulance")
    request_medical_advice = forms.BooleanField(required=False, label="I also need advice from a medical professional")
    emergency_type = forms.ChoiceField(choices=EmergencyRequest.Type.choices, label="Emergency category")
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, max_length=1000,
                                  label="Symptoms / what happened")
    contact_phone = forms.CharField(max_length=20, label="Phone number responders can call")
    latitude = forms.FloatField(min_value=-90, max_value=90, widget=forms.HiddenInput)
    longitude = forms.FloatField(min_value=-180, max_value=180, widget=forms.HiddenInput)
    location_accuracy_m = forms.FloatField(required=False, widget=forms.HiddenInput)
    location_source = forms.ChoiceField(choices=[("gps", "gps"), ("manual", "manual")], widget=forms.HiddenInput)
    location_consent = forms.BooleanField(required=False, widget=forms.HiddenInput)
    location_description = forms.CharField(max_length=255, required=False,
                                           label="Address or landmark (helps the crew find you)")
    conscious = forms.BooleanField(required=False, initial=True, label="The patient is conscious / responsive")
    breathing_difficulty = forms.BooleanField(required=False, label="Difficulty breathing")
    severe_bleeding = forms.BooleanField(required=False, label="Severe bleeding")
    chest_pain = forms.BooleanField(required=False, label="Chest pain")
    pregnant = forms.BooleanField(required=False, label="Pregnant")
    pain_level = forms.IntegerField(min_value=0, max_value=10, initial=0, label="Pain level (0-10)")
    age = forms.IntegerField(min_value=0, max_value=120, required=False, label="Patient age (if not you)")
    # Double submissions are blocked server-side by services.find_duplicate()
    # and client-side by disabling the submit button.

    def clean(self):
        c = super().clean()
        if c.get("latitude") is None or c.get("longitude") is None:
            raise forms.ValidationError("Please share your GPS location or pick your location on the map.")
        if c.get("location_source") == "gps" and not c.get("location_consent"):
            raise forms.ValidationError("GPS location can only be used with your permission.")
        if not c.get("request_ambulance") and not c.get("request_medical_advice"):
            c["request_ambulance"] = True  # an emergency request with no service ticked still gets an ambulance
        c["patient_condition"] = c.get("patient_condition") or "unknown"
        if c.get("patient_condition") == "unresponsive":
            c["conscious"] = False  # keep the AI input consistent with what the caller reported
        return c

    def to_emergency_data(self):
        return dict(self.cleaned_data)
