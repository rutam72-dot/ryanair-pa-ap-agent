/**
 * Ryanair PA/AP — free Google Apps Script calendar bridge.
 * Paste into https://script.google.com/ while signed in as rutam72@gmail.com.
 * Default: DRY_RUN=true. No events are modified until explicitly changed.
 * Reads only confirmed PLN fares from public GitHub history.
 */
const CONFIG = Object.freeze({
  HISTORY_URL: 'https://raw.githubusercontent.com/rutam72-dot/ryanair-pa-ap-agent/main/state/history.json',
  CALENDAR_IDS: ['rutam72@gmail.com', 'b.prokglock@gmail.com', 'ryanairsamolot@gmail.com'],
  DRY_RUN: true,
  LOOKAHEAD_DAYS: 180,
  CHANGE_THRESHOLD_PERCENT: 10,
  TAG: 'ryanair-pa-ap-agent:v1'
});

function todayWarsaw_() {
  return Utilities.formatDate(new Date(), 'Europe/Warsaw', 'yyyy-MM-dd');
}
function addDays_(ymd, n) {
  const date = new Date(ymd + 'T12:00:00Z');
  date.setUTCDate(date.getUTCDate() + n);
  return Utilities.formatDate(date, 'UTC', 'yyyy-MM-dd');
}
function parseDate_(ymd) {
  return new Date(ymd + 'T00:00:00Z');
}
function fetchHistory_() {
  const response = UrlFetchApp.fetch(CONFIG.HISTORY_URL, {muteHttpExceptions: true, followRedirects: true});
  if (response.getResponseCode() !== 200) {
    throw new Error('GitHub history HTTP ' + response.getResponseCode());
  }
  const data = JSON.parse(response.getContentText());
  if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('Invalid history format');
  const today = todayWarsaw_();
  const limit = addDays_(today, CONFIG.LOOKAHEAD_DAYS);
  const fares = {};
  Object.keys(data).forEach(key => {
    const match = /^(PA|AP):(\d{4}-\d{2}-\d{2})$/.exec(key);
    if (!match) throw new Error('Invalid route/date: ' + key);
    if (match[2] < today || match[2] > limit) return;
    const value = Number(data[key]);
    if (!Number.isFinite(value) || value < 0) throw new Error('Invalid price: ' + key);
    fares[key] = value;
  });
  return fares;
}
function priceLabel_(value) {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
}
function marker_(kind, key, observed) {
  return '[' + CONFIG.TAG + '|' + kind + '|' + key + (observed ? '|' + observed : '') + ']';
}
function findManaged_(calendar, day, marker) {
  return calendar.getEventsForDay(parseDate_(day)).filter(event =>
    (event.getDescription() || '').indexOf(marker) !== -1
  );
}
function syncOne_(calendar, key, price, changes, observed, stats) {
  const day = key.split(':')[1];
  const route = key.split(':')[0];
  const title = route + '•' + priceLabel_(price) + 'zł';
  const marker = marker_('fare', key);
  const existing = findManaged_(calendar, day, marker);
  if (existing.length > 1) stats.duplicates += existing.length - 1;
  if (existing.length === 0) {
    stats.inserts++;
    if (!CONFIG.DRY_RUN) calendar.createAllDayEvent(title, parseDate_(day), {
      description: marker + '\nRyanair PLN; monitoring ceny, nie rezerwacja.'
    });
  } else if (existing[0].getTitle() !== title) {
    stats.updates++;
    if (!CONFIG.DRY_RUN) existing[0].setTitle(title);
  }
  if (!changes[key]) return;
  const change = changes[key];
  const bellTitle = '🔔' + change.percent + '% ' + route;
  const bellMarker = marker_('alert', key, observed);
  const bells = findManaged_(calendar, day, bellMarker);
  if (bells.length > 1) stats.duplicates += bells.length - 1;
  if (!bells.length) {
    stats.alerts++;
    if (!CONFIG.DRY_RUN) {
      const bell = calendar.createAllDayEvent(bellTitle, parseDate_(day), {
        description: bellMarker + '\nZmiana ceny Ryanair: ' + change.direction
      });
      bell.setColor(change.direction === 'down' ? CalendarApp.EventColor.GREEN : CalendarApp.EventColor.RED);
    }
  }
}
function calculateChanges_(previous, current) {
  const result = {};
  Object.keys(current).forEach(key => {
    const old = Number(previous[key]);
    const next = current[key];
    if (!Object.prototype.hasOwnProperty.call(previous, key) || !Number.isFinite(old) || old <= 0) return;
    const percent = Math.abs((next - old) / old * 100);
    if (percent >= CONFIG.CHANGE_THRESHOLD_PERCENT) {
      result[key] = {percent: Math.round(percent), direction: next < old ? 'down' : 'up'};
    }
  });
  return result;
}
function syncRyanair() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) throw new Error('Another sync is running');
  try {
    const properties = PropertiesService.getScriptProperties();
    const current = fetchHistory_();
    const previous = JSON.parse(properties.getProperty('LAST_SYNC_FARES') || '{}');
    const observed = todayWarsaw_();
    const changes = calculateChanges_(previous, current);
    const report = {};
    CONFIG.CALENDAR_IDS.forEach(id => {
      const calendar = CalendarApp.getCalendarById(id);
      if (!calendar) throw new Error('Calendar unavailable: ' + id);
      const stats = {inserts: 0, updates: 0, alerts: 0, duplicates: 0};
      Object.keys(current).sort().forEach(key => syncOne_(calendar, key, current[key], changes, observed, stats));
      report[id] = stats;
    });
    console.log(JSON.stringify({dryRun: CONFIG.DRY_RUN, fares: Object.keys(current).length, calendars: report}));
    if (!CONFIG.DRY_RUN) {
      properties.setProperty('LAST_SYNC_FARES', JSON.stringify(current));
      properties.setProperty('LAST_SUCCESS', new Date().toISOString());
    }
    return report;
  } finally {
    lock.releaseLock();
  }
}
/** Call manually only after testing and changing DRY_RUN to false. */
function installFourHourTrigger() {
  if (CONFIG.DRY_RUN) throw new Error('First confirm test, then set DRY_RUN=false');
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncRyanair').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('syncRyanair').timeBased().everyHours(4).create();
  console.log('Four-hour trigger installed (Google scheduling is approximate).');
}
function checkStatus() {
  const properties = PropertiesService.getScriptProperties();
  console.log(JSON.stringify({
    dryRun: CONFIG.DRY_RUN,
    lastSuccess: properties.getProperty('LAST_SUCCESS') || 'Never',
    triggerCount: ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'syncRyanair').length
  }));
}
