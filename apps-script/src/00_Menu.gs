/**
 * DocuFunnel — Apps Script edition
 *
 * Mail with attachments in, structured rows out. Same five stages as the
 * Python package (source, store, normalize, extract, sink) but collapsed into
 * a spreadsheet so there is nothing to install.
 *
 * Why this edition exists: Apps Script already runs *as the signed-in user*.
 * It needs no OAuth client, no app password, and no service account to read
 * that person's mail or write their sheet. The only secret is a Gemini API
 * key. That is the difference between a setup a non-technical person finishes
 * and one they abandon.
 *
 * Normalization has no stage here on purpose: Gemini reads a PDF directly, so
 * page layout survives and scanned documents work without a separate OCR
 * step. The Python edition keeps MarkItDown and Docling for the cases this
 * cannot reach — non-PDF formats, and cheaper text-only extraction at volume.
 */

var MENU_NAME = 'DocuFunnel';

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu(MENU_NAME)
    .addItem('Set up (start here)', 'menuSetup')
    .addSeparator()
    .addItem('Test run — reads mail, writes nothing', 'menuTestRun')
    .addItem('Run now', 'menuRun')
    .addSeparator()
    .addItem('Run automatically every hour', 'menuScheduleHourly')
    .addItem('Run automatically once a day', 'menuScheduleDaily')
    .addItem('Stop running automatically', 'menuUnschedule')
    .addSeparator()
    .addItem('Check my setup', 'menuDoctor')
    .addItem('Change Gemini API key', 'menuSetApiKey')
    .addToUi();
}

function onInstall(e) {
  onOpen(e);
}

function menuSetup() {
  runSetup();
}

function menuRun() {
  runPipeline({ dryRun: false });
}

function menuTestRun() {
  runPipeline({ dryRun: true });
}

function menuDoctor() {
  showDoctor();
}

function menuSetApiKey() {
  promptForApiKey(true);
}

function menuScheduleHourly() {
  createSchedule('hourly');
}

function menuScheduleDaily() {
  createSchedule('daily');
}

/** The function time-based triggers call. Kept separate so the trigger target
 *  never changes even if the menu wiring does. */
function scheduledRun() {
  runPipeline({ dryRun: false, scheduled: true });
}
