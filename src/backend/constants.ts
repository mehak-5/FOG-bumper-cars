export const IPC = {
  GET_STATE: 'app:get-state',
  TRIGGER_KEY: 'trigger:key',
  STATE: 'app:state',
  NOTICE: 'app:notice',

  // Previews & readiness
  PREVIEW_FRAME: 'preview:frame',
  READINESS: 'preview:readiness',
  DISK_STATUS: 'disk:status',

  // Session
  SESSION_NOTES: 'session:notes',

  // Config
  CONFIG_CAMERAS_GET: 'config:cameras:get',
  CONFIG_CAMERAS_SAVE: 'config:cameras:save',
  CONFIG_SETTINGS_GET: 'config:settings:get',
  CONFIG_SETTINGS_SAVE: 'config:settings:save',

  // History
  HISTORY_LIST: 'history:list',
  HISTORY_DELETE: 'history:delete',
  HISTORY_OPEN_FOLDER: 'history:open-folder',

  // Health checks
  HEALTH_CHECK_INTERNET: 'health:check-internet',
  HEALTH_CHECK_SERVER: 'health:check-server',
  HEALTH_CHECK_CAMERAS: 'health:check-cameras',
} as const;
