import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  ValidationRequest,
  WorkflowApiService,
} from '../shared/workflow-api.service';

@Component({
  selector: 'app-validation-requests',
  imports: [CommonModule, FormsModule],
  templateUrl: './validation-requests.html',
  styleUrl: './validation-requests.scss',
})
export class ValidationRequests {
  private workflowApi = inject(WorkflowApiService);

  requests: ValidationRequest[] = [];
  loading = false;
  processingId = '';
  error = '';
  statusFilter = 'SUBMITTED';
  reasonById: Record<string, string> = {};

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';
    this.workflowApi.validationRequests(this.statusFilter as any).subscribe({
      next: (requests) => {
        this.requests = requests;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les demandes de validation.';
        this.loading = false;
      },
    });
  }

  approve(request: ValidationRequest) {
    this.review(request, 'APPROVED');
  }

  reject(request: ValidationRequest) {
    const reason = this.reasonById[request.id]?.trim();
    if (!reason) {
      this.reasonById[request.id] = 'Motif requis';
      return;
    }
    this.review(request, 'REJECTED', reason);
  }

  entityLabel(type: string) {
    const labels: Record<string, string> = {
      PREFECTURE: 'Préfecture',
      SUB_PREFECTURE: 'Sous-préfecture',
      SCHOOL: 'École',
      TEACHER: 'Enseignant',
    };
    return labels[type] ?? type;
  }

  statusLabel(status: string) {
    const labels: Record<string, string> = {
      SUBMITTED: 'À valider',
      APPROVED: 'Validée',
      REJECTED: 'Rejetée',
      DRAFT: 'Brouillon',
    };
    return labels[status] ?? status;
  }

  private review(request: ValidationRequest, status: 'APPROVED' | 'REJECTED', reason?: string) {
    this.processingId = request.id;
    this.workflowApi.reviewValidationRequest(request.id, status, reason).subscribe({
      next: () => {
        this.processingId = '';
        this.load();
      },
      error: () => {
        this.processingId = '';
        this.error = 'La validation n’a pas pu être enregistrée.';
      },
    });
  }
}
