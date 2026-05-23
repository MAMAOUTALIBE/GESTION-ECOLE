import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { RouterModule } from '@angular/router';
import { ApexOptions } from 'ng-apexcharts';
import { SpkApexcharts } from '../../../@spk/charts/spk-apexcharts/spk-apexcharts';
import {
  AcademicValidationStatus,
  AssessmentType,
} from '../../school-census/shared/school-census.models';

interface AcademicKpi {
  label: string;
  value: string;
  detail: string;
  icon: string;
  color: string;
  trend: string;
}

interface AcademicPeriodMetric {
  period: string;
  average: number;
  target: number;
  participation: number;
}

interface ExamVolumeMetric {
  label: string;
  planned: number;
  validated: number;
}

interface SubjectPerformance {
  subject: string;
  average: number;
  exams: number;
  status: 'stable' | 'attention' | 'excellent';
}

interface UpcomingAssessment {
  id: string;
  title: string;
  type: AssessmentType;
  subject: string;
  className: string;
  date: string;
  status: AcademicValidationStatus;
}

interface ClassRisk {
  className: string;
  school: string;
  average: number;
  missingGrades: number;
  trend: number;
}

@Component({
  selector: 'app-dashboard-2',
  imports: [CommonModule, RouterModule, SpkApexcharts],
  templateUrl: './dashboard-2.html',
  styleUrl: './dashboard-2.scss',
})
export class Dashboard2 {
  readonly kpis: AcademicKpi[] = [
    {
      label: 'Moyenne générale',
      value: '13,8 / 20',
      detail: 'Toutes classes consolidées',
      icon: 'ri-bar-chart-grouped-line',
      color: 'primary',
      trend: '+0,6 pt',
    },
    {
      label: 'Élèves évalués',
      value: '18 420',
      detail: 'Notes saisies ce trimestre',
      icon: 'ri-graduation-cap-line',
      color: 'success',
      trend: '+8,4%',
    },
    {
      label: 'Examens planifiés',
      value: '326',
      detail: 'Devoirs, compositions et nationaux',
      icon: 'ri-calendar-check-line',
      color: 'info',
      trend: '42 cette semaine',
    },
    {
      label: 'Bulletins validés',
      value: '74%',
      detail: 'Workflow académique terminé',
      icon: 'ri-file-check-line',
      color: 'warning',
      trend: '+12%',
    },
  ];

  readonly periodMetrics: AcademicPeriodMetric[] = [
    { period: 'Oct', average: 12.8, target: 13.5, participation: 82 },
    { period: 'Nov', average: 13.1, target: 13.5, participation: 86 },
    { period: 'Déc', average: 13.6, target: 13.5, participation: 91 },
    { period: 'Jan', average: 13.4, target: 14, participation: 88 },
    { period: 'Fév', average: 13.9, target: 14, participation: 93 },
    { period: 'Mar', average: 14.2, target: 14, participation: 95 },
  ];

  readonly examVolumes: ExamVolumeMetric[] = [
    { label: 'Interros', planned: 84, validated: 72 },
    { label: 'Devoirs', planned: 116, validated: 91 },
    { label: 'Compositions', planned: 74, validated: 58 },
    { label: 'Examens nat.', planned: 52, validated: 36 },
  ];

  readonly validationSummary = [
    { status: 'DRAFT' as AcademicValidationStatus, label: 'Brouillons', value: 18 },
    { status: 'SUBMITTED' as AcademicValidationStatus, label: 'Soumis', value: 22 },
    { status: 'VALIDATED' as AcademicValidationStatus, label: 'Validés', value: 54 },
    { status: 'REJECTED' as AcademicValidationStatus, label: 'À reprendre', value: 6 },
  ];

  readonly subjectPerformance: SubjectPerformance[] = [
    { subject: 'Mathématiques', average: 12.9, exams: 64, status: 'attention' },
    { subject: 'Français', average: 14.1, exams: 58, status: 'stable' },
    { subject: 'Sciences', average: 13.7, exams: 46, status: 'stable' },
    { subject: 'Histoire-Géo', average: 15.2, exams: 38, status: 'excellent' },
    { subject: 'Anglais', average: 13.4, exams: 42, status: 'stable' },
  ];

  readonly upcomingAssessments: UpcomingAssessment[] = [
    {
      id: 'exam-001',
      title: 'Composition T2',
      type: 'COMPOSITION',
      subject: 'Mathématiques',
      className: '10ème A',
      date: '2026-05-08',
      status: 'SUBMITTED',
    },
    {
      id: 'exam-002',
      title: 'Examen blanc BEPC',
      type: 'NATIONAL_EXAM',
      subject: 'Français',
      className: '10ème',
      date: '2026-05-14',
      status: 'DRAFT',
    },
    {
      id: 'exam-003',
      title: 'Projet sciences',
      type: 'PROJECT',
      subject: 'Sciences',
      className: '9ème B',
      date: '2026-05-18',
      status: 'VALIDATED',
    },
    {
      id: 'exam-004',
      title: 'Oral anglais',
      type: 'ORAL',
      subject: 'Anglais',
      className: '8ème C',
      date: '2026-05-21',
      status: 'SUBMITTED',
    },
  ];

  readonly riskClasses: ClassRisk[] = [
    { className: '10ème A', school: 'Lycée Donka', average: 10.8, missingGrades: 34, trend: -0.7 },
    { className: '9ème B', school: 'Collège Matoto', average: 11.2, missingGrades: 21, trend: -0.4 },
    { className: '8ème C', school: 'Collège Kankan 2', average: 11.5, missingGrades: 18, trend: 0.2 },
    { className: '11ème SM', school: 'Lycée Nongo', average: 12.1, missingGrades: 12, trend: 0.5 },
  ];

  readonly averageTrendChart: ApexOptions = {
    series: [
      {
        name: 'Moyenne observée',
        data: this.periodMetrics.map((item) => item.average),
      },
      {
        name: 'Objectif',
        data: this.periodMetrics.map((item) => item.target),
      },
    ],
    chart: { type: 'area', height: 330, toolbar: { show: false } },
    colors: ['var(--primary-color)', '#23b7e5'],
    dataLabels: { enabled: false },
    fill: {
      type: 'gradient',
      gradient: { shadeIntensity: 0.2, opacityFrom: 0.35, opacityTo: 0.05 },
    },
    grid: { borderColor: 'var(--default-border)' },
    legend: { show: true, position: 'top' },
    markers: { size: 4 },
    stroke: { curve: 'smooth', width: 3 },
    xaxis: { categories: this.periodMetrics.map((item) => item.period) },
    yaxis: {
      min: 8,
      max: 20,
      labels: {
        formatter: (value) => value.toFixed(0),
      },
    },
    tooltip: {
      y: {
        formatter: (value) => `${value.toFixed(1)} / 20`,
      },
    },
  };

  readonly examVolumeChart: ApexOptions = {
    series: [
      {
        name: 'Planifiés',
        data: this.examVolumes.map((item) => item.planned),
      },
      {
        name: 'Validés',
        data: this.examVolumes.map((item) => item.validated),
      },
    ],
    chart: { type: 'bar', height: 310, toolbar: { show: false } },
    colors: ['var(--primary-color)', '#26bf94'],
    dataLabels: { enabled: false },
    grid: { borderColor: 'var(--default-border)' },
    legend: { show: true, position: 'top' },
    plotOptions: {
      bar: { borderRadius: 4, columnWidth: '42%' },
    },
    xaxis: { categories: this.examVolumes.map((item) => item.label) },
    yaxis: {
      labels: {
        formatter: (value) => value.toFixed(0),
      },
    },
  };

  readonly validationChart: ApexOptions = {
    series: this.validationSummary.map((item) => item.value),
    chart: { type: 'donut', height: 286 },
    colors: ['#f5b849', '#23b7e5', '#26bf94', '#e6533c'],
    dataLabels: { enabled: false },
    labels: this.validationSummary.map((item) => item.label),
    legend: { show: false },
    plotOptions: {
      pie: {
        donut: {
          size: '72%',
          labels: {
            show: true,
            total: {
              show: true,
              label: 'Dossiers',
            },
          },
        },
      },
    },
  };

  readonly subjectRadarChart: ApexOptions = {
    series: [
      {
        name: 'Moyenne',
        data: this.subjectPerformance.map((item) => item.average),
      },
    ],
    chart: { type: 'radar', height: 310, toolbar: { show: false } },
    colors: ['#845adf'],
    dataLabels: { enabled: true },
    markers: { size: 4 },
    stroke: { width: 2 },
    xaxis: { categories: this.subjectPerformance.map((item) => item.subject) },
    yaxis: {
      min: 0,
      max: 20,
      labels: {
        formatter: (value) => value.toFixed(0),
      },
    },
  };

  readonly participationChart: ApexOptions = {
    series: [
      {
        name: 'Participation',
        data: this.periodMetrics.map((item) => item.participation),
      },
    ],
    chart: { type: 'line', height: 92, sparkline: { enabled: true } },
    colors: ['#26bf94'],
    stroke: { curve: 'smooth', width: 3 },
    tooltip: {
      y: {
        formatter: (value) => `${value.toFixed(0)}%`,
      },
    },
  };

  formatType(type: AssessmentType) {
    const labels: Record<AssessmentType, string> = {
      QUIZ: 'Interrogation',
      HOMEWORK: 'Devoir',
      COMPOSITION: 'Composition',
      NATIONAL_EXAM: 'Examen national',
      ORAL: 'Oral',
      PROJECT: 'Projet',
      OTHER: 'Autre',
    };

    return labels[type];
  }

  statusLabel(status: AcademicValidationStatus) {
    const labels: Record<AcademicValidationStatus, string> = {
      DRAFT: 'Brouillon',
      SUBMITTED: 'Soumis',
      VALIDATED: 'Validé',
      REJECTED: 'Rejeté',
    };

    return labels[status];
  }

  statusClass(status: AcademicValidationStatus) {
    const classes: Record<AcademicValidationStatus, string> = {
      DRAFT: 'bg-warning-transparent text-warning',
      SUBMITTED: 'bg-info-transparent text-info',
      VALIDATED: 'bg-success-transparent text-success',
      REJECTED: 'bg-danger-transparent text-danger',
    };

    return classes[status];
  }

  subjectStatusClass(status: SubjectPerformance['status']) {
    const classes: Record<SubjectPerformance['status'], string> = {
      attention: 'bg-warning-transparent text-warning',
      stable: 'bg-info-transparent text-info',
      excellent: 'bg-success-transparent text-success',
    };

    return classes[status];
  }

  formatNumber(value: number) {
    return value.toLocaleString('fr-FR');
  }
}
