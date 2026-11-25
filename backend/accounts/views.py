from rest_framework import generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import PatientProfile
from .serializers import PatientProfileSerializer, RegisterSerializer, UserSerializer


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class PatientProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = PatientProfileSerializer

    def get_object(self):
        profile, _ = PatientProfile.objects.get_or_create(user=self.request.user)
        return profile


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def register_fcm_token(request):
    request.user.fcm_token = request.data.get("token", "")
    request.user.save(update_fields=["fcm_token"])
    return Response({"status": "ok"})
