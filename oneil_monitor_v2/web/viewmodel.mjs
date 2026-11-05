export const stateGroups = Object.freeze({QUEUED:'pending', VALIDATING:'pending', SUBMISSION_UNKNOWN:'unknown', ACKNOWLEDGED:'pending', PART_FILLED:'pending', FILLED:'filled', CANCELLED:'closed', REJECTED:'closed', EXPIRED:'closed', BLOCKED:'closed'});
export function observedStatus(evidence, elapsedSeconds=0) {
  if (!evidence || evidence.status === 'missing') return 'missing';
  if (evidence.status === 'clock_error' || evidence.age_seconds < 0) return 'clock_error';
  if (!Number.isFinite(evidence.age_seconds) || !Number.isFinite(evidence.max_age_seconds)) return 'missing';
  return evidence.age_seconds + Math.max(0, elapsedSeconds) <= evidence.max_age_seconds ? 'fresh' : 'stale';
}
const labels = Object.freeze({QUEUED:'대기',VALIDATING:'검증 중',SUBMISSION_UNKNOWN:'결과 미확인',ACKNOWLEDGED:'브로커 접수',PART_FILLED:'부분 체결',FILLED:'체결 완료',CANCELLED:'취소',REJECTED:'거절',EXPIRED:'만료',BLOCKED:'차단'});
function utcStamp(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value) && Number.isFinite(Date.parse(value));
}
export function validModel(value, expectedSource='AUTHENTICATED_SIMULATION_CORE') {
  if (!['AUTHENTICATED_SIMULATION_CORE','MOCK_AUTHENTICATED_SIMULATION'].includes(expectedSource)) return false;
  if (!value || value.schema_version !== 'monitor.v1' || value.mode !== 'SIMULATION' || value.source !== expectedSource || value.live_capable !== false || value.read_only !== true) return false;
  if (value.owner_authorization?.state !== (expectedSource === 'AUTHENTICATED_SIMULATION_CORE' ? 'AUTHENTICATED_READ_ONLY' : 'MOCK_SESSION_READ_ONLY')) return false;
  if (typeof value.control?.halted !== 'boolean' || !Number.isInteger(value.control?.revision) || value.control.revision < 1) return false;
  if (!Array.isArray(value.requests) || value.requests.length > 100 || !value.counts || !value.request_window) return false;
  if (!value.controls || ['halt','resume_simulation','cancel_pending','confirm_order'].some(key=>value.controls[key]?.enabled !== false)) return false;
  if (!utcStamp(value.server_time)) return false;
  for (const item of [value.snapshot, value.heartbeat, value.broker_sync]) {
    if (!item || !['fresh','stale','missing','clock_error'].includes(item.status) || !Number.isSafeInteger(item.max_age_seconds) || item.max_age_seconds <= 0) return false;
    if (item.status === 'missing') {
      if (item.age_seconds !== null || item.observed_at !== null) return false;
    } else {
      if (!Number.isFinite(item.age_seconds) || !utcStamp(item.observed_at)) return false;
      const expectedAge=(Date.parse(value.server_time)-Date.parse(item.observed_at))/1000;
      if (Math.abs(expectedAge-item.age_seconds) > 0.001) return false;
      // Date.parse truncates Python microseconds. Age is authoritative after the
      // timestamp consistency check, preserving exact boundary behavior.
      const expectedStatus=item.age_seconds < 0 ? 'clock_error' : item.age_seconds <= item.max_age_seconds ? 'fresh' : 'stale';
      if (item.status !== expectedStatus) return false;
    }
  }
  const ids = new Set(), counts = {pending:0, unknown:0, filled:0, closed:0};
  for (const row of value.requests) {
    if (!row || typeof row.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(row.request_id) || ids.has(row.request_id)) return false;
    ids.add(row.request_id);
    if (typeof row.state !== 'string' || row.label !== (labels[row.state] || '알 수 없는 상태') || typeof row.reason !== 'string' || row.group !== (stateGroups[row.state] || 'unknown')) return false;
    if (!Number.isInteger(row.attempts) || row.attempts < 0 || row.attempts > 1 || !Number.isInteger(row.filled_quantity) || row.filled_quantity < 0) return false;
    if (row.filled_quantity > 0 && row.attempts !== 1) return false;
    if (['ACKNOWLEDGED','QUEUED','VALIDATING','REJECTED','BLOCKED','EXPIRED'].includes(row.state) && row.filled_quantity !== 0) return false;
    if (['PART_FILLED','FILLED'].includes(row.state) && row.filled_quantity <= 0) return false;
    if (['SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED','FILLED'].includes(row.state) && row.attempts !== 1) return false;
    if (['QUEUED','VALIDATING','BLOCKED','EXPIRED'].includes(row.state) && row.attempts !== 0) return false;
    if (![row.created_at,row.updated_at].every(utcStamp)) return false;
    if (Date.parse(row.created_at) > Date.parse(row.updated_at) || Date.parse(row.updated_at) > Date.parse(value.snapshot.observed_at)) return false;
    if (row.order_details_available !== false || row.can_cancel_before_admission !== (['QUEUED','VALIDATING'].includes(row.state) && row.attempts === 0)) return false;
    counts[row.group]++;
  }
  return Object.keys(counts).every(group=>counts[group] === value.counts[group]);
}
export function filterRequests(requests, filter) {
  return filter === 'all' ? requests : requests.filter(row=>row.group === filter);
}
export function actionableWarning(model, elapsedSeconds=0, disconnected=false) {
  if (disconnected) return 'READ_FAILED';
  if (!model) return 'NO_SNAPSHOT';
  if (model.counts.unknown > 0) return 'UNKNOWN_OUTCOME';
  if (observedStatus(model.snapshot, elapsedSeconds) !== 'fresh') return 'SNAPSHOT_NOT_FRESH';
  if (observedStatus(model.heartbeat, elapsedSeconds) !== 'fresh') return 'WORKER_NOT_FRESH';
  if (observedStatus(model.broker_sync, elapsedSeconds) !== 'fresh') return 'BROKER_NOT_FRESH';
  return model.control.halted ? 'HALTED' : 'READ_ONLY';
}
export class LatestRequestGate {
  #current=0;
  begin() { return ++this.#current; }
  current(token) { return token === this.#current; }
}
