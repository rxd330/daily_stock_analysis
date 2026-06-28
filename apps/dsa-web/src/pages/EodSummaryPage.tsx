import React, { useCallback, useEffect, useState } from 'react';
import { Calendar, RefreshCw, CheckCircle2, FileText } from 'lucide-react';
import { portfolioDigestApi } from '../api/analysis';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, Button, EmptyState, InlineAlert, Loading } from '../components/common';
import { AppPage } from '../components/common/AppPage';
import type { PortfolioDigestResponse, StockFreshness } from '../types/analysis';

const EodSummaryPage: React.FC = () => {
  // State
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>(() => new Date().toISOString().slice(0, 10));
  const [digest, setDigest] = useState<PortfolioDigestResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  // Load available dates on mount
  const loadAvailableDates = useCallback(async () => {
    try {
      const result = await portfolioDigestApi.getAvailableDates();
      setAvailableDates(result.dates);
    } catch {
      // Silent — dates will just show today
    }
  }, []);

  useEffect(() => {
    void loadAvailableDates();
  }, [loadAvailableDates]);

  // Generate digest
  const generateDigest = useCallback(async (date?: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await portfolioDigestApi.generate({ date: date || selectedDate, lang: 'zh' });
      setDigest(result);
    } catch (err) {
      setError(getParsedApiError(err));
      setDigest(null);
    } finally {
      setLoading(false);
    }
  }, [selectedDate]);

  // Format date display
  const formatDate = (dateStr: string) => {
    try {
      const d = new Date(dateStr + 'T00:00:00');
      return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });
    } catch {
      return dateStr;
    }
  };

  // Freshness badge
  const FreshnessBadge: React.FC<{ freshness: StockFreshness[] }> = ({ freshness }) => {
    if (!freshness || freshness.length === 0) return null;
    return (
      <div className="flex flex-wrap gap-2 mb-4">
        {freshness.map((f) => (
          <Badge
            key={f.code}
            variant={f.fresh ? 'success' : f.ageDays === 1 ? 'warning' : 'danger'}
          >
            {f.code}
            {f.fresh ? ' 今日' : f.ageDays === 1 ? ' 昨日' : ` ${f.ageDays}天前`}
          </Badge>
        ))}
      </div>
    );
  };

  return (
    <AppPage>
      <h1 className="text-xl font-semibold text-foreground mb-4">EOD 总结</h1>
      <div className="flex flex-col gap-4">
        {/* Controls */}
        <div className="flex flex-wrap items-end gap-3">
          {/* Date selector */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs text-secondary-text font-medium">日期</label>
            <select
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="input min-w-[180px]"
            >
              {availableDates.length === 0 && (
                <option value={selectedDate}>{formatDate(selectedDate)}</option>
              )}
              {availableDates.map((d) => (
                <option key={d} value={d}>
                  {formatDate(d)}
                  {d === new Date().toISOString().slice(0, 10) ? ' (今天)' : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Generate button */}
          <Button
            onClick={() => generateDigest()}
            disabled={loading}
            className="flex items-center gap-2"
          >
            {loading ? (
              <>
                <Loading />
                <span className="text-sm text-secondary-text ml-2">生成中...</span>
              </>
            ) : (
              <>
                <RefreshCw className="h-4 w-4" />
                生成总结
              </>
            )}
          </Button>
        </div>

        {/* Staleness warning banner */}
        {digest && !digest.isToday && (
          <InlineAlert
            variant="warning"
            title="非今日数据"
            message={`当前显示的是 ${digest.targetDate} 的总结，非今日数据。数据可能已过时。`}
          />
        )}
        {digest?.anyStale && (
          <InlineAlert
            variant="warning"
            title="部分数据过时"
            message="部分股票的个股分析不是最新的，已在总结中标注。建议重新运行分析后刷新。"
          />
        )}

        {/* Errors */}
        {error && <ApiErrorAlert error={error} />}

        {/* Empty state */}
        {!loading && !digest && !error && (
          <EmptyState
            icon={<FileText className="h-12 w-12 text-muted-text" />}
            title="还没有今日总结"
            description="选择日期后点击「生成总结」，系统将读取个股分析报告并生成投资组合级别概述。"
          />
        )}

        {/* No data state */}
        {digest?.status === 'no_data' && (
          <EmptyState
            icon={<Calendar className="h-12 w-12 text-muted-text" />}
            title="该日期无分析报告"
            description={digest.error || '请先在首页运行个股分析，然后再生成总结。'}
          />
        )}

        {/* Digest result */}
        {digest?.status === 'ok' && digest.digestText && (
          <div className="flex flex-col gap-3">
            {/* Meta bar */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
              <span className="flex items-center gap-1">
                <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                {digest.stockCount} 只股票
              </span>
              {digest.modelUsed && (
                <span>· 模型: {digest.modelUsed}</span>
              )}
              <span>· {digest.targetDate}</span>
            </div>

            {/* Freshness badges */}
            {digest.stocksFreshness && <FreshnessBadge freshness={digest.stocksFreshness} />}

            {/* Digest text */}
            <div className="card p-4">
              <div className="prose prose-sm max-w-none dark:prose-invert whitespace-pre-wrap text-sm leading-relaxed">
                {digest.digestText}
              </div>
            </div>
          </div>
        )}

        {/* Error state */}
        {digest?.status === 'error' && (
          <InlineAlert
            variant="danger"
            title="生成失败"
            message={digest.error || '生成失败，请重试。'}
          />
        )}
      </div>
    </AppPage>
  );
};

export default EodSummaryPage;
