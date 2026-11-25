from rest_framework import serializers

from .models import PatientProfile, User


class PatientProfileSerializer(serializers.ModelSerializer):
    age = serializers.IntegerField(read_only=True)

    class Meta:
        model = PatientProfile
        exclude = ("user",)


class UserSerializer(serializers.ModelSerializer):
    patient_profile = PatientProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "email", "phone", "role", "hospital", "patient_profile")
        read_only_fields = ("role", "hospital")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=[User.Role.PATIENT, User.Role.PARAMEDIC], default=User.Role.PATIENT)

    class Meta:
        model = User
        fields = ("username", "password", "first_name", "last_name", "email", "phone", "role")

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        if user.role == User.Role.PATIENT:
            PatientProfile.objects.create(user=user)
        return user
