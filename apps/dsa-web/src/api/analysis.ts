import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  AnalysisRequest,
  AnalysisResult,
  AnalyzeResponse,
  AnalyzeAsyncResponse,
  AnalysisReport,
  MarketReviewAccepted,
  MarketReviewRequest,
  TaskStatus,
  TaskListResponse,
} from '../types/analysis';
import type { RunFlowSnapshot } from '../types/runFlow';

// ============ API Interfaces ============

export const analysisApi = {
  /**
   * Trigger stock analysis.
   * @param data Analysis request payload
   * @returns Sync mode returns AnalysisResult; async mode returns accepted task payloads
   */
  analyze: async (data: AnalysisRequest): Promise<AnalyzeResponse> => {
    const requestData = {
      stock_code: data.stockCode,
      stock_codes: data.stockCodes,
      report_type: data.reportType || 'detailed',
      force_refresh: data.forceRefresh || false,
      async_mode: data.asyncMode || false,
      analysis_phase: data.analysisPhase || 'auto',
      stock_name: data.stockName,
      original_query: data.originalQuery,
      selection_source: data.selectionSource,
      skills: data.skills,
      report_language: data.reportLanguage,
      ...(data.notify !== undefined && { notify: data.notify }),
    };

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/analysis/analyze',
      requestData
    );

    const result = toCamelCase<AnalyzeResponse>(response.data);

    // Ensure the sync analysis report payload is converted recursively.
    if ('report' in result && result.report) {
      result.report = toCamelCase<AnalysisReport>(result.report);
    }

    return result;
  },

  /**
   * Trigger analysis in async mode.
   * @param data Analysis request payload
   * @returns Accepted task payloads; throws DuplicateTaskError on 409
   */
  analyzeAsync: async (data: AnalysisRequest): Promise<AnalyzeAsyncResponse> => {
    const requestData = {
      stock_code: data.stockCode,
      stock_codes: data.stockCodes,
      report_type: data.reportType || 'detailed',
      force_refresh: data.forceRefresh || false,
      async_mode: true,
      analysis_phase: data.analysisPhase || 'auto',
      stock_name: data.stockName,
      original_query: data.originalQuery,
      selection_source: data.selectionSource,
      skills: data.skills,
      report_language: data.reportLanguage,
      ...(data.notify !== undefined && { notify: data.notify }),
    };

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/analysis/analyze',
      requestData,
      {
        // Allow 202 accepted responses in addition to standard success codes.
        validateStatus: (status) => status === 200 || status === 202 || status === 409,
      }
    );

    // Handle duplicate submission compatibility.
    if (response.status === 409) {
      const errorData = toCamelCase<{
        error: string;
        message: string;
        stockCode: string;
        existingTaskId: string;
      }>(response.data);
      throw new DuplicateTaskError(errorData.stockCode, errorData.existingTaskId, errorData.message);
    }

    return toCamelCase<AnalyzeAsyncResponse>(response.data);
  },

  /**
   * Trigger market review in background mode.
   */
  triggerMarketReview: async (data: MarketReviewRequest = {}): Promise<MarketReviewAccepted> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/analysis/market-review',
      {
        send_notification: data.sendNotification ?? true,
        report_language: data.reportLanguage,
      },
      {
        validateStatus: (status) => status === 202 || status === 409,
      }
    );

    if (response.status === 409) {
      const detail = response.data?.detail;
      const message = detail && typeof detail === 'object' && 'message' in detail
        ? String((detail as { message?: unknown }).message || '')
        : String(response.data?.message || '');
      throw new Error(message || '大盘复盘正在执行中，请稍后再试');
    }

    return toCamelCase<MarketReviewAccepted>(response.data);
  },

  /**
   * Get async task status.
   * @param taskId Task ID
   */
  getStatus: async (taskId: string): Promise<TaskStatus> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/analysis/status/${taskId}`
    );

    const data = toCamelCase<TaskStatus>(response.data);

    // Ensure nested result payloads are converted recursively.
    if (data.result) {
      data.result = toCamelCase<AnalysisResult>(data.result);
      if (data.result.report) {
        data.result.report = toCamelCase<AnalysisReport>(data.result.report);
      }
    }

    return data;
  },

  /**
   * Get task list.
   * @param params Filter parameters
   */
  getTasks: async (params?: {
    status?: string;
    limit?: number;
  }): Promise<TaskListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/analysis/tasks',
      { params }
    );

    const data = toCamelCase<TaskListResponse>(response.data);

    return data;
  },

  /**
   * Get a run-flow snapshot for an active analysis task.
   * @param taskId Task ID
   */
  getTaskFlow: async (taskId: string): Promise<RunFlowSnapshot> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/analysis/tasks/${encodeURIComponent(taskId)}/flow`
    );

    return toCamelCase<RunFlowSnapshot>(response.data);
  },

  /**
   * Get the SSE stream URL.
   */
  getTaskStreamUrl: (): string => {
    // Read API base URL from the shared client.
    const baseUrl = apiClient.defaults.baseURL || '';
    return `${baseUrl}/api/v1/analysis/tasks/stream`;
  },
};

// ============ Custom Error Classes ============

/**
 * Duplicate task error.
 */
export class DuplicateTaskError extends Error {
  stockCode: string;
  existingTaskId: string;

  constructor(stockCode: string, existingTaskId: string, message?: string) {
    super(message || `股票 ${stockCode} 正在分析中`);
    this.name = 'DuplicateTaskError';
    this.stockCode = stockCode;
    this.existingTaskId = existingTaskId;
  }
}

// ============ Portfolio Digest ============

import type { PortfolioDigestResponse } from '../types/analysis';

export const portfolioDigestApi = {
  /** Generate portfolio-level EOD summary */
  generate: async (params: {
    date?: string;
    codes?: string;
    lang?: string;
  }): Promise<PortfolioDigestResponse> => {
    const searchParams = new URLSearchParams();
    if (params.date) searchParams.set('date', params.date);
    if (params.codes) searchParams.set('codes', params.codes);
    if (params.lang) searchParams.set('lang', params.lang);
    const qs = searchParams.toString();
    const url = `/api/v1/analysis/portfolio-digest${qs ? '?' + qs : ''}`;
    const response = await apiClient.post<PortfolioDigestResponse>(url);
    return response.data;
  },

  /** Get dates with available reports */
  getAvailableDates: async (days = 30): Promise<{ dates: string[]; count: number }> => {
    const response = await apiClient.get<{ dates: string[]; count: number }>(
      `/api/v1/analysis/available-digest-dates?days=${days}`
    );
    return response.data;
  },
};

// ============ Watchlist ============

export interface WatchlistItem {
  code: string;
  name: string | null;
  active: boolean;
  addedAt: string | null;
}

export const watchlistApi = {
  /** Get all active watchlist items */
  list: async (): Promise<{ items: WatchlistItem[]; count: number }> => {
    const response = await apiClient.get<{ items: WatchlistItem[]; count: number }>(
      '/api/v1/analysis/watchlist'
    );
    return response.data;
  },

  /** Add a stock to the watchlist */
  add: async (code: string): Promise<{ status: string; code: string }> => {
    const response = await apiClient.post<{ status: string; code: string }>(
      `/api/v1/analysis/watchlist/add?code=${encodeURIComponent(code)}`
    );
    return response.data;
  },

  /** Remove a stock from the watchlist */
  remove: async (code: string): Promise<{ status: string; code: string }> => {
    const response = await apiClient.post<{ status: string; code: string }>(
      `/api/v1/analysis/watchlist/remove?code=${encodeURIComponent(code)}`
    );
    return response.data;
  },
};
