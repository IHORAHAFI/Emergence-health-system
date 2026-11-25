from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import PatientProfileForm, PatientRegistrationForm, ProfessionalRegistrationForm, UserForm
from .models import PatientProfile


def home(request):
    """Send each user to the right place after login."""
    u = request.user
    if not u.is_authenticated:
        return redirect("landing")
    if u.role == "patient":
        return redirect("patient_home")
    if u.role == "paramedic":
        return redirect("paramedic_home")
    return redirect("dashboard")


def register_patient(request):
    form = PatientRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome! Please complete your medical profile.")
        return redirect("profile")
    return render(request, "portal/register.html", {"form": form, "kind": "Patient"})


def register_professional(request):
    form = ProfessionalRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Registration received. An administrator must verify your licence "
                                  "before you can access patient information. You can log in after approval.")
        return redirect("login")
    return render(request, "portal/register.html", {"form": form, "kind": "Healthcare professional"})


@login_required
def profile(request):
    uform = UserForm(request.POST or None, instance=request.user, prefix="u")
    pform = None
    if request.user.role == "patient":
        prof, _ = PatientProfile.objects.get_or_create(user=request.user)
        pform = PatientProfileForm(request.POST or None, instance=prof, prefix="p")
    if request.method == "POST" and uform.is_valid() and (pform is None or pform.is_valid()):
        uform.save()
        if pform:
            pform.save()
        messages.success(request, "Profile saved.")
        return redirect("profile")
    return render(request, "portal/profile.html", {"uform": uform, "pform": pform})
