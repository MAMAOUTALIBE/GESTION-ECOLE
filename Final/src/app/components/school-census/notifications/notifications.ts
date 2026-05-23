import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { AppNotification, WorkflowApiService } from '../shared/workflow-api.service';

@Component({
  selector: 'app-notifications',
  imports: [CommonModule],
  templateUrl: './notifications.html',
  styleUrl: './notifications.scss',
})
export class Notifications {
  private workflowApi = inject(WorkflowApiService);

  notifications: AppNotification[] = [];
  loading = false;
  error = '';

  ngOnInit() {
    this.load();
  }

  load() {
    this.loading = true;
    this.error = '';
    this.workflowApi.notifications().subscribe({
      next: (notifications) => {
        this.notifications = notifications;
        this.loading = false;
      },
      error: () => {
        this.error = 'Impossible de charger les notifications.';
        this.loading = false;
      },
    });
  }

  markRead(notification: AppNotification) {
    if (notification.isRead) {
      return;
    }
    this.workflowApi.markNotificationRead(notification.id).subscribe({
      next: () => {
        notification.isRead = true;
      },
    });
  }
}
