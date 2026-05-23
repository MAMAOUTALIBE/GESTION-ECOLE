import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { environment } from '../../../../environments/environment';

// =====================================================================
// Types (miroir des schémas Pydantic backend — Phase 11)
// =====================================================================
export type BudgetStatus = 'DRAFT' | 'APPROVED' | 'ACTIVE' | 'CLOSED';
export type BudgetCategory =
  | 'SALARIES' | 'INFRASTRUCTURE' | 'EQUIPMENT' | 'OPERATIONS'
  | 'TRAINING' | 'TRANSPORT' | 'MEALS' | 'MISC';
export type ExpenseStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'PAID';
export type PolicyUnitCostCode =
  | 'NEW_SCHOOL' | 'NEW_CLASSROOM' | 'TEACHER_YEAR'
  | 'GIRLS_TOILETS' | 'ELECTRICITY_SOLAR' | 'WATER_BOREHOLE';

export interface BudgetRead {
  id: string;
  fiscalYear: number;
  category: BudgetCategory;
  status: BudgetStatus;
  regionId: string | null;
  prefectureId: string | null;
  subPrefectureId: string | null;
  schoolId: string | null;
  amountPlanned: number;
  amountSpent: number;
  amountRemaining: number;
  consumptionRate: number;
  currency: string;
  notes: string | null;
  createdById: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface BudgetPage {
  rows: BudgetRead[];
  total: number;
  page: number;
  pageSize: number;
}

export interface CategoryBreakdown {
  category: BudgetCategory;
  planned: number;
  spent: number;
  remaining: number;
  consumptionRate: number;
}

export interface BudgetStats {
  fiscalYear: number | null;
  totalPlanned: number;
  totalSpent: number;
  totalRemaining: number;
  consumptionRate: number;
  overspendingBudgets: number;
  breakdownByCategory: CategoryBreakdown[];
}

export interface CreateBudgetRequest {
  fiscalYear: number;
  category: BudgetCategory;
  amountPlanned: number;
  currency?: string;
  regionId?: string | null;
  prefectureId?: string | null;
  subPrefectureId?: string | null;
  schoolId?: string | null;
  notes?: string | null;
}

export interface UpdateBudgetRequest {
  status?: BudgetStatus;
  amountPlanned?: number;
  notes?: string | null;
}

export interface ExpenseRead {
  id: string;
  budgetId: string | null;
  category: BudgetCategory;
  amount: number;
  currency: string;
  description: string;
  expenseDate: string;
  status: ExpenseStatus;
  schoolId: string | null;
  regionId: string | null;
  prefectureId: string | null;
  subPrefectureId: string | null;
  approvedById: string | null;
  approvedAt: string | null;
  receiptUrl: string | null;
  createdById: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ExpensePage {
  rows: ExpenseRead[];
  total: number;
  page: number;
  pageSize: number;
}

export interface CreateExpenseRequest {
  budgetId?: string | null;
  category: BudgetCategory;
  amount: number;
  currency?: string;
  description: string;
  expenseDate: string;
  schoolId?: string | null;
  regionId?: string | null;
  prefectureId?: string | null;
  subPrefectureId?: string | null;
  receiptUrl?: string | null;
}

export interface UpdateExpenseStatusRequest {
  status: ExpenseStatus;
  note?: string | null;
}

export interface UnitCostRead {
  id: string;
  code: PolicyUnitCostCode;
  label: string;
  amount: number;
  currency: string;
  source: string | null;
  isActive: boolean;
  updatedById: string | null;
  updatedAt: string;
}

export interface UnitCostsResponse {
  rows: UnitCostRead[];
}

export interface UpsertUnitCostRequest {
  code: PolicyUnitCostCode;
  label: string;
  amount: number;
  currency?: string;
  source?: string | null;
  isActive?: boolean;
}

export interface BudgetListQuery {
  fiscalYear?: number;
  category?: BudgetCategory;
  status?: BudgetStatus;
  schoolId?: string;
  regionId?: string;
  page?: number;
  pageSize?: number;
}

export interface ExpenseListQuery {
  budgetId?: string;
  schoolId?: string;
  category?: BudgetCategory;
  status?: ExpenseStatus;
  page?: number;
  pageSize?: number;
}

@Injectable({ providedIn: 'root' })
export class FinanceApiService {
  private http = inject(HttpClient);
  private baseUrl = `${environment.apiUrl}/finance`;

  // ---- Budgets ----
  listBudgets(query: BudgetListQuery = {}) {
    return this.http.get<BudgetPage>(`${this.baseUrl}/budgets`, {
      params: this.params(query),
    });
  }

  budgetStats(fiscalYear?: number) {
    let params = new HttpParams();
    if (fiscalYear !== undefined) {
      params = params.set('fiscalYear', String(fiscalYear));
    }
    return this.http.get<BudgetStats>(`${this.baseUrl}/budgets/stats`, { params });
  }

  getBudget(id: string) {
    return this.http.get<BudgetRead>(`${this.baseUrl}/budgets/${id}`);
  }

  createBudget(payload: CreateBudgetRequest) {
    return this.http.post<BudgetRead>(`${this.baseUrl}/budgets`, payload);
  }

  updateBudget(id: string, payload: UpdateBudgetRequest) {
    return this.http.patch<BudgetRead>(`${this.baseUrl}/budgets/${id}`, payload);
  }

  // ---- Expenses ----
  listExpenses(query: ExpenseListQuery = {}) {
    return this.http.get<ExpensePage>(`${this.baseUrl}/expenses`, {
      params: this.params(query),
    });
  }

  createExpense(payload: CreateExpenseRequest) {
    return this.http.post<ExpenseRead>(`${this.baseUrl}/expenses`, payload);
  }

  updateExpenseStatus(id: string, payload: UpdateExpenseStatusRequest) {
    return this.http.patch<ExpenseRead>(`${this.baseUrl}/expenses/${id}`, payload);
  }

  // ---- Unit costs (référentiel simulator) ----
  listUnitCosts() {
    return this.http.get<UnitCostsResponse>(`${this.baseUrl}/unit-costs`);
  }

  upsertUnitCost(payload: UpsertUnitCostRequest) {
    return this.http.put<UnitCostRead>(`${this.baseUrl}/unit-costs`, payload);
  }

  private params(query: object): HttpParams {
    let params = new HttpParams();
    Object.entries(query as Record<string, unknown>).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    });
    return params;
  }
}
