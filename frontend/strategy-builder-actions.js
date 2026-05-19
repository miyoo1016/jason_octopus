    function copyAll() {
      if (!state.results) return alert('먼저 분석을 실행하세요.');
      const nr = state.results.node_results || {};
      const nodeIds = Object.keys(nr);
      if (!nodeIds.length) return alert('데이터가 없습니다.');
      
      const structuredRows = typeof collectStructuredRows === 'function' ? collectStructuredRows(state.results) : [];
      const data = structuredRows.length ? structuredRows : (nr[nodeIds[nodeIds.length - 1]]?.data || []);
      
      if (!data.length) return alert('복사할 데이터가 없습니다.');

      const enabled = Array.from(state.enabledSignals);
      const today = new Date().toISOString().slice(0, 10);
      let topRecsText = '';
      if (state.results && state.results.summary && state.results.summary.top_recommendations && state.results.summary.top_recommendations.length > 0) {
        const topRecs = state.results.summary.top_recommendations;
        topRecsText = `[오늘의 추천 TOP 3]`;
        const hasBuyNow = topRecs.some(r => r.action === 'BUY_NOW');
        const warningLine = !hasBuyNow ? `\nBUY_NOW 없음. 조건부/소액탐색 후보만 존재` : '';
        const recsLines = '\n' + topRecs.map((r, idx) => `${idx + 1}. ${r.name} — ${r.action} / ${r.size}% / 점수 ${r.score}`).join('\n');
        topRecsText += warningLine + recsLines + `\n`;
      }

      const header = [
        `[AlphaForge 시스템 분석 요약]`,
        `분석 일자: ${today}`,
        `대상 시장: ${state.market || 'KR'}`,
        `분류 완료: ${data.length}건`,
        ``,
        topRecsText ? topRecsText : '',
        `[종목 상세 리스트]`,
      ].join('\n').replace(/\n\n\n/g, '\n\n');

      // 화면용 관찰 라벨 → 텍스트 매핑
      const _alertTypeText = {
        'BUY_CANDIDATE': '◎ [BUY CANDIDATE]',
        'NEAR_BUY': '○ [NEAR BUY]',
        'PRIORITY_WATCH': '★ [PRIORITY WATCH]',
        'RISK_WATCH':   '⚠️ [RISK WATCH]',
        'SETUP_WATCH':  '◇ [SETUP WATCH]',
      };

      const rows = data.map((row, i) => {
        const bucket = getPrimaryBucket(row);
        const alertType = typeof getDisplayWatchAlertType === 'function' ? getDisplayWatchAlertType(row) : (row.display_watch_alert_type || row.watch_alert_type || 'NONE');
        const watchAlert = getWatchAlert(row) ? (_alertTypeText[alertType] || '') : '';
        const scoreInfo = getScoreInfo(row, state.results);

        // display_promotion_reasons 우선 (getDisplayPromotionReasons 함수가 있으면 사용)
        let displayReasonList = [];
        if (typeof getDisplayPromotionReasons === 'function') {
          displayReasonList = getDisplayPromotionReasons(row);
        } else {
          displayReasonList = (row.display_promotion_reasons && row.display_promotion_reasons.length)
            ? row.display_promotion_reasons
            : (row.tier_promotion_reasons && row.tier_promotion_reasons.length)
              ? row.tier_promotion_reasons
              : (row.watchlist_reasons || []);
        }
        
        // CSV/Clipboard용 문자열: display_promotion_reasons_str이 있으면 사용, 없으면 join
        const rejectedList = typeof getDisplayRejectedReasons === 'function' ? getDisplayRejectedReasons(row) : (row.display_rejected_reasons || row.rejected_reasons || []);
        const reasonList = bucket === 'REJECTED' ? rejectedList : displayReasonList;
        const promo = bucket === 'REJECTED'
          ? (row.display_rejected_reasons_str || (Array.isArray(reasonList) ? reasonList.join('; ') : String(reasonList || '')))
          : (row.display_promotion_reasons_str || (Array.isArray(reasonList) ? reasonList.join('; ') : String(reasonList || '')));
        const promoVal = promo && promo !== '없음' && promo !== '[]' ? promo : (bucket === 'REJECTED' ? '세부 제외 사유 미기록' : '없음');
        
        const downList = row.display_restriction_reasons || row.downgrade_reasons || [];
        const down = row.display_restriction_reasons_str || row.downgrade_reasons_str || (Array.isArray(downList) ? downList.join('; ') : String(downList || ''));
        const downVal = down && down !== '없음' && down !== '[]' ? down : '없음';
        const failedBuyGates = row.failed_buy_gates || row.failedBuyGates || [];
        const buyGateText = row.buy_gate_passed || row.buyGatePassed
          ? 'PASS'
          : (Array.isArray(failedBuyGates) ? failedBuyGates.join('; ') : String(failedBuyGates || row.buy_gate_reason || row.buyGateReason || ''));

        const mc = typeof formatCap === 'function' ? formatCap(row.market_cap) : (row.market_cap || '-');
        const flowText = row.flow_total_score != null ? row.flow_total_score : (row.flow_score ?? '-');

        // 추천 필드 추출 (alias 허용)
        const recAction = row.recommendation_action || row.recommendationAction || 'WATCH_ONLY';
        let recRank = row.recommendation_rank || row.recommendationRank || 'N/A';
        if (recRank === 'N/A' && state.results && state.results.summary && state.results.summary.top_recommendations) {
            const topRec = state.results.summary.top_recommendations.find(r => r.code === row.code);
            if (topRec) recRank = topRec.rank;
        }
        let recScoreRaw = row.recommendation_score ?? row.recommendationScore;
        const recScore = (recScoreRaw == null || Number.isNaN(Number(recScoreRaw)) || recScoreRaw === 'N/A' || recScoreRaw === '') ? 0 : recScoreRaw;
        const recSize = row.suggested_position_size || row.suggestedPositionSize || 0;
        const recReason = row.recommendation_reason || row.recommendationReason || '';
        let recTrigger = row.entry_trigger || row.entryTrigger || '';
        let recInvalidation = row.invalidation_condition || row.invalidationCondition || '';

        if (recAction === 'WATCH_ONLY') {
            if (!recTrigger) recTrigger = 'VCP 재형성 + 돌파 거래량 확인 전까지 진입 금지';
            if (!recInvalidation) recInvalidation = 'RS 80 이탈, 이평선 비정렬 지속, 변동성 확장 지속';
        } else if (recAction === 'AVOID') {
            if (!recTrigger) recTrigger = '매수 대상 아님. RS/VCP/이평선/수급 조건 재충족 시 재평가';
            if (!recInvalidation) recInvalidation = '회피 유지. 복합 약점 해소 전까지 진입 금지';
        }

        // TIER → '승격 사유', WATCHLIST/CRISIS_HOLD → '관찰 사유', REJECTED → '제외 사유'
        const promoLabel = bucket.startsWith('TIER') ? '승격 사유'
          : (bucket === 'WATCHLIST' || bucket === 'CRISIS_HOLD') ? '관찰 사유'
          : '제외 사유';

        return [
          `${i + 1}. ${row.name || ''} (${row.code || ''}) ${watchAlert}`,
          `   - 분류: ${bucket} | ${row.tier_reason || ''}`,
          `   - 총점: ${scoreInfo.totalScore} / ${scoreInfo.scoreMax} (Tier ${row.tier || '-'})`,
          `   - ${promoLabel}: ${promoVal}`,
          `   - 제한 사유: ${downVal}`,
          `   - BUY 게이트: ${buyGateText || '없음'}`,
          `   - 지표: RS ${row.rs_rating || 0}, VCP ${row.vcp_score || 0}, 수급 ${flowText}`,
          `   - VCP 진단: ${row.vcp_diagnostic || row.vcp_quality_reason || row.vcpQualityReason || `raw ${row.vcp_raw_score ?? '-'} → effective ${row.vcp_effective_score ?? '-'} → display ${row.vcp_display_score ?? row.vcp_score ?? '-'}`}`,
          `   - 추천: ${recAction} | 순위 ${recRank} | 점수 ${recScore} | 권장비중 ${recSize}%`,
          `   - 추천 사유: ${recReason}`,
          `   - 진입 트리거: ${recTrigger}`,
          `   - 무효화 조건: ${recInvalidation}`,
          `   - 유동성: ${row.liquidity_status || '-'} (${typeof formatCap === 'function' ? formatCap(row.liquidity_trading_value) : (row.liquidity_trading_value || '-')})`,
          `     (Vol: ${row.liquidity_volume || 0} / Source: ${row.liquidity_volume_source || ''} / Suspicious: ${row.volume_suspicious ? '🔴 YES' : 'NO'})`,
          `   - 시총: ${mc} | 섹터: ${row.sector || '기타'}`,
          `--------------------------------------------------------`
        ].join('\n');
      });

      const text = header + '\n' + rows.join('\n');

      navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('btnCopyAll');
        btn.textContent = '✅ 복사됨';
        setTimeout(() => { btn.textContent = '📋 전체 복사'; }, 1500);
      }).catch(() => alert('클립보드 복사에 실패했습니다.'));
    }

    function exportCSV() {
      if (!state.results) return alert('먼저 분석을 실행하세요.');
      const nr = state.results.node_results || {};
      const nodeIds = Object.keys(nr);
      if (!nodeIds.length) return;
      
      const structuredRows = typeof collectStructuredRows === 'function' ? collectStructuredRows(state.results) : [];
      const data = structuredRows.length ? structuredRows : (nr[nodeIds[nodeIds.length - 1]]?.data || []);
      if (!data.length) return;
      
      const priorityCols = [
        "code", "name", "primary_bucket", "watchlist_flag", "watch_alert_type", "legacy_label", "display_watch_alert_type", "display_label", "final_label",
        "buy_gate_passed", "failed_buy_gates", "buy_gate_reason",
        "action_alert_flag", "tier", "total_score", "score_max",
        "tier_reason", "display_promotion_reasons", "display_rejected_reasons", "promotion_reasons", "rejected_reasons",
        "display_restriction_reasons", "downgrade_reasons", "sector", "market_cap",
        "vcp_score", "vcp_raw_score", "vcp_effective_score", "vcp_display_score", "vcp_status", "vcp_confidence",
        "vcp_cross_warning", "vcp_component_scores", "vcp_quality_reason", "vcp_penalty_reasons", "candidate_confidence", "vcp_diagnostic",
        "vcp_warning", "breakout_status", "box_breakout_grade",
        "rs_rating", "flow_total_score", "foreign_net_buy", "institution_net_buy",
        "liquidity_status", "liquidity_trading_value", "liquidity_trading_value_source",
        "liquidity_close", "liquidity_volume", "liquidity_volume_source", "liquidity_volume_raw",
        "volume_suspicious", "liquidity_data_warning", "liquidity_reason", "watch_exclusion_reason"
      ];
      
      const firstRow = data[0];
      const allCols = Object.keys(firstRow);
      const cols = priorityCols.filter(c => allCols.includes(c));
      allCols.forEach(c => { if (!cols.includes(c)) cols.push(c); });

      let csv = '\uFEFF' + cols.join(',') + '\n';
      data.forEach(r => {
        csv += cols.map(c => {
          let val = r[c] ?? '';
          if (Array.isArray(val)) val = val.join('; ');
          if (val && typeof val === 'object') val = JSON.stringify(val);
          val = String(val);
          if (val.includes(',') || val.includes('\n') || val.includes('"')) {
            val = `"${val.replace(/"/g, '""')}"`;
          }
          return val;
        }).join(',') + '\n';
      });
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `alphaforge_${new Date().toISOString().slice(0,10)}.csv`;
      a.click();
    }
