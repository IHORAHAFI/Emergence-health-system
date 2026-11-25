"""Hospital management pages for administrators, operators and healthcare professionals."""
from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from emergency.web_views import dispatch_required, staff_required, _is_dispatcher
from .models import Hospital, Specialty

ACTIVE = ["completed", "cancelled"]


class HospitalForm(forms.ModelForm):
    class Meta:
        model = Hospital
        fields = ("name", "level", "address", "phone", "emergency_phone", "email", "latitude", "longitude",
                  "specialties", "has_emergency_unit", "emergency_department_info", "total_beds",
                  "available_beds", "icu_available", "is_active")
        labels = {"specialties": "Available services", "has_emergency_unit": "Has an emergency department",
                  "is_active": "Operating (shown to patients and used for recommendations)"}
        widgets = {"specialties": forms.CheckboxSelectMultiple,
                   "emergency_department_info": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for name, f in self.fields.items():
            w = f.widget
            if isinstance(w, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                w.attrs.setdefault("class", "form-check-input")
            else:
                w.attrs.setdefault("class", "form-select" if isinstance(w, forms.Select) else "form-control")

    def clean(self):
        c = super().clean()
        if c.get("latitude") is not None and not -90 <= c["latitude"] <= 90:
            self.add_error("latitude", "Latitude must be between -90 and 90.")
        if c.get("longitude") is not None and not -180 <= c["longitude"] <= 180:
            self.add_error("longitude", "Longitude must be between -180 and 180.")
        if c.get("available_beds") is not None and c.get("total_beds") is not None \
                and c["available_beds"] > c["total_beds"]:
            self.add_error("available_beds", "Available beds cannot exceed total beds.")
        return c


@staff_required
def hospital_list(request):
    qs = Hospital.objects.prefetch_related("specialties").annotate(
        incoming=Count("incoming_emergencies", filter=~Q(incoming_emergencies__status__in=ACTIVE)))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(address__icontains=q))
    if request.GET.get("service"):
        qs = qs.filter(specialties__code=request.GET["service"])
    if request.GET.get("ed") == "1":
        qs = qs.filter(has_emergency_unit=True)
    page = Paginator(qs.distinct(), 12).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "hospitals/hospital_list.html", {
        "page": page, "q": q, "params": params.urlencode(), "services": Specialty.objects.order_by("name"),
        "can_edit": _is_dispatcher(request.user), "all_hospitals": qs.distinct()})


@staff_required
def hospital_detail(request, pk):
    h = get_object_or_404(Hospital.objects.prefetch_related("specialties"), pk=pk)
    u = request.user
    # Incoming patients are only shown to dispatchers and to this hospital's own staff.
    can_see_patients = _is_dispatcher(u) or u.hospital_id == h.id
    incoming = []
    if can_see_patients:
        incoming = h.incoming_emergencies.exclude(status__in=ACTIVE).select_related(
            "assigned_ambulance", "assigned_ambulance__driver").order_by("eta_minutes")
    return render(request, "hospitals/hospital_detail.html", {
        "h": h, "incoming": incoming, "can_see_patients": can_see_patients, "can_edit": _is_dispatcher(u),
        "staff": h.staff.filter(is_active=True) if _is_dispatcher(u) else []})


@dispatch_required
def hospital_edit(request, pk=None):
    h = get_object_or_404(Hospital, pk=pk) if pk else None
    old_beds = (h.available_beds, h.icu_available, h.total_beds) if h else None
    form = HospitalForm(request.POST or None, instance=h)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if old_beds != (obj.available_beds, obj.icu_available, obj.total_beds):
            # Only the hospital's own staff can confirm capacity (Capacity page).
            obj.capacity_verified = False
            obj.capacity_updated_by = None
        obj.save()
        form.save_m2m()
        messages.success(request, f"{obj.name} saved." + ("" if obj.capacity_verified else
                         " Bed figures are marked UNVERIFIED until the hospital's staff confirm them."))
        return redirect("hospital_detail", pk=obj.pk)
    return render(request, "hospitals/hospital_form.html", {"form": form, "h": h})
