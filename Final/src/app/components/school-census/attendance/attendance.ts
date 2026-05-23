import { CommonModule } from '@angular/common';
import { Component, ElementRef, ViewChild, inject } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { CensusApiService } from '../shared/census-api.service';
import { AttendanceRecord, AttendanceStatus } from '../shared/school-census.models';

@Component({
  selector: 'app-attendance',
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: './attendance.html',
  styleUrl: './attendance.scss',
})
export class Attendance {
  @ViewChild('video') video?: ElementRef<HTMLVideoElement>;

  private api = inject(CensusApiService);
  private formBuilder = inject(FormBuilder);
  private stream: MediaStream | null = null;
  private scanning = false;

  records: AttendanceRecord[] = [];
  lastRecord: AttendanceRecord | null = null;
  duplicate = false;
  loading = false;
  error = '';
  cameraError = '';
  statuses: Array<{ value: AttendanceStatus; label: string }> = [
    { value: 'PRESENT', label: 'Présent' },
    { value: 'LATE', label: 'Retard' },
    { value: 'ABSENT', label: 'Absent' },
  ];

  form = this.formBuilder.group({
    qrToken: ['', Validators.required],
    status: ['PRESENT' as AttendanceStatus, Validators.required],
  });

  ngOnInit() {
    this.loadToday();
  }

  loadToday() {
    this.loading = true;
    this.api.todayAttendance().subscribe({
      next: (records) => {
        this.records = records;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les présences du jour.';
        this.loading = false;
      },
    });
  }

  scan() {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }

    const rawValue = this.form.controls.qrToken.value ?? '';
    const token = this.extractToken(rawValue);
    const status = this.form.controls.status.value ?? 'PRESENT';
    this.error = '';

    this.api.scan(token, status).subscribe({
      next: (result) => {
        this.duplicate = result.duplicate;
        this.lastRecord = result.record;
        this.records = [result.record, ...this.records.filter((record) => record.id !== result.record.id)];
        this.form.reset({ qrToken: '', status });
      },
      error: () => {
        this.error = 'QR code invalide ou hors périmètre.';
      },
    });
  }

  async startCamera() {
    this.cameraError = '';

    if (!('BarcodeDetector' in window)) {
      this.cameraError = 'Lecture caméra indisponible sur ce navigateur.';
      return;
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment' },
      });
      if (this.video?.nativeElement) {
        this.video.nativeElement.srcObject = this.stream;
        await this.video.nativeElement.play();
      }
      this.scanning = true;
      this.detectLoop();
    } catch {
      this.cameraError = 'Impossible d’accéder à la caméra.';
    }
  }

  stopCamera() {
    this.scanning = false;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  ngOnDestroy() {
    this.stopCamera();
  }

  private async detectLoop() {
    const detector = new (window as any).BarcodeDetector({ formats: ['qr_code'] });

    while (this.scanning && this.video?.nativeElement) {
      const video = this.video.nativeElement;
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        const barcodes = await detector.detect(video);
        const value = barcodes[0]?.rawValue;
        if (value) {
          this.form.patchValue({ qrToken: value });
          this.stopCamera();
          this.scan();
          return;
        }
      }
      await new Promise((resolve) => setTimeout(resolve, 350));
    }
  }

  private extractToken(value: string) {
    const cleaned = value.trim();
    const withoutQuery = cleaned.split('?')[0];
    const parts = withoutQuery.split('/').filter(Boolean);
    return parts[parts.length - 1] || cleaned;
  }

  statusLabel(status: AttendanceStatus) {
    const labels: Record<AttendanceStatus, string> = {
      PRESENT: 'Présent',
      LATE: 'Retard',
      ABSENT: 'Absent',
    };

    return labels[status];
  }

  statusClass(status: AttendanceStatus) {
    const classes: Record<AttendanceStatus, string> = {
      PRESENT: 'bg-success-transparent text-success',
      LATE: 'bg-warning-transparent text-warning',
      ABSENT: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }
}
