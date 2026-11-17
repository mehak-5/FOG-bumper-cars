/**
 * HealthCheckModal / MomentsStatusModal
 * -------------------------------------
 * Stylized in exact accordance with the MomentsStatusModal reference:
 * - Clean modal container styled with dark aesthetics & crisp typography
 * - Grid row layout: [leading icon] [label + sub-label] [trailing status icon]
 * - Color palette:
 *     pending:  #6b6b6b (dimmed)
 *     checking: #ffffff
 *     success:  #25AE88 (emerald green)
 *     warning:  #F5A623 (amber/orange)
 *     error:    #D75A4A (coral red)
 * - Sub-label alert styling with #f0ea00 warning/contact highlights
 * - "Don't Show Again in this session" checkbox + Retry / Ok action buttons
 * - Sequential checking: Internet -> Moments Server -> Cameras
 */
import {
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  Typography,
} from '@mui/material';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { CameraReadiness, HealthCheckResult } from '../../../backend/shared/types';

// -------------------------------------------------------------------------
// Types & Palette
// -------------------------------------------------------------------------

export type CheckState = 'pending' | 'checking' | 'success' | 'warning' | 'error';

const COLORS: Record<CheckState, string> = {
  pending: '#6b6b6b',
  checking: '#ffffff',
  success: '#25AE88',
  warning: '#F5A623',
  error: '#D75A4A',
};

const ICON_COLORS: Record<CheckState, string> = {
  pending: '#555555',
  checking: '#ffffff',
  success: '#25AE88',
  warning: '#F5A623',
  error: '#D75A4A',
};

const CONTACT_SUPPORT = 'Please contact FOG support';

// -------------------------------------------------------------------------
// Vector Icons (clean inline SVG, zero external package dependencies)
// -------------------------------------------------------------------------

function NetworkIcon({ color }: { color: string }) {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill={color}>
      <path d="M15.9 5c-.17 0-.32.09-.41.23l-.07.15-5.18 10.37-2.6-5.19A.498.498 0 0 0 7.2 10.2H1v1.6h5.68l3.12 6.24c.09.18.27.29.47.29.18 0 .34-.09.43-.24l.05-.1 5.37-10.74 1.88 3.75c.08.17.26.28.45.28h4.55V10.2h-4.08l-2.5-5c-.09-.12-.23-.2-.39-.2z"/>
    </svg>
  );
}

function ServerIcon({ color }: { color: string }) {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill={color}>
      <path d="M2 20h20v-4H2v4zm2-3h2v2H4v-2zM2 4v4h20V4H2zm4 3H4V5h2v2zm-4 7h20v-4H2v4zm2-3h2v2H4v-2z"/>
    </svg>
  );
}

function VideocamIcon({ color }: { color: string }) {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill={color}>
      <path d="M17 10.5V7c0-.55-.45-1-1-1H4c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-3.5l4 4v-11l-4 4z"/>
    </svg>
  );
}

function SuccessSvg() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="#25AE88">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
    </svg>
  );
}

function ErrorSvg() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="#D75A4A">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
    </svg>
  );
}

function WarningSvg() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="#F5A623">
      <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>
    </svg>
  );
}

// -------------------------------------------------------------------------
// CheckRow component
// -------------------------------------------------------------------------
const CheckRow = ({
  icon,
  state,
  label,
  sub,
}: {
  icon: React.ReactNode;
  state: CheckState;
  label: string;
  sub?: string;
}) => {
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: '36px 1fr 28px',
        alignItems: 'center',
        columnGap: '1rem',
        padding: '0.65rem 1rem',
        minHeight: '54px',
        boxSizing: 'border-box',
        borderRadius: '6px',
        backgroundColor: state === 'error' ? 'rgba(215, 90, 74, 0.08)' : 'transparent',
        transition: 'background-color 0.2s ease',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {icon}
      </Box>

      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: '0.2rem',
          textAlign: 'left',
        }}
      >
        <Typography
          sx={{
            color: COLORS[state],
            fontSize: '1.02rem',
            fontWeight: 600,
            letterSpacing: '0.6px',
            lineHeight: 1.25,
          }}
        >
          {label}
        </Typography>

        {sub && (
          <Typography
            sx={{
              color: '#f0ea00',
              fontSize: '0.8rem',
              fontWeight: 600,
              letterSpacing: '0.4px',
              lineHeight: 1.2,
            }}
          >
            {sub}
          </Typography>
        )}
      </Box>

      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {state === 'checking' ? (
          <CircularProgress size={20} thickness={4} sx={{ color: '#ffffff' }} />
        ) : state === 'success' ? (
          <SuccessSvg />
        ) : state === 'warning' ? (
          <WarningSvg />
        ) : state === 'error' ? (
          <ErrorSvg />
        ) : null}
      </Box>
    </Box>
  );
};

// -------------------------------------------------------------------------
// Main MomentsStatusModal (replacing HealthCheckModal contents)
// -------------------------------------------------------------------------
export function HealthCheckModal({
  open,
  onClose,
  readiness,
}: {
  open: boolean;
  onClose: () => void;
  readiness: Record<string, CameraReadiness>;
}) {
  const [isOpen, setIsOpen] = useState(open);
  const [dontShow, setDontShow] = useState(false);
  const [retrying, setRetrying] = useState(false);

  // States for the 3 sequential checks
  const [internet, setInternet] = useState<CheckState>('pending');
  const [server, setServer] = useState<CheckState>('pending');
  const [cameras, setCameras] = useState<CheckState>('pending');

  const [internetAttempt, setInternetAttempt] = useState(1);
  const [serverAttempt, setServerAttempt] = useState(1);
  const [cameraCount, setCameraCount] = useState(0);
  const [camerasExpected, setCamerasExpected] = useState(2);

  const abortRef = useRef(false);

  useEffect(() => {
    setIsOpen(open);
  }, [open]);

  // Sequential execution: Internet -> Server -> Cameras
  const runChecks = useCallback(async () => {
    if (retrying) return;
    abortRef.current = false;
    setRetrying(true);

    // Reset rows to pending
    setInternet('checking');
    setServer('pending');
    setCameras('pending');
    setInternetAttempt(1);

    // ── 1. INTERNET CHECK (up to 9 attempts) ─────────────────────────
    let netResult: HealthCheckResult = { ok: false, message: '', attempts: 0 };
    for (let attempt = 1; attempt <= 9; attempt++) {
      if (abortRef.current) {
        setRetrying(false);
        return;
      }
      setInternetAttempt(attempt);
      try {
        netResult = await window.bumper.checkInternet(1);
        if (netResult.ok) break;
      } catch {
        netResult = { ok: false, message: 'Failed', attempts: attempt };
      }
      if (attempt < 9) {
        await new Promise((r) => setTimeout(r, 800));
      }
    }

    if (abortRef.current) {
      setRetrying(false);
      return;
    }

    if (!netResult.ok) {
      setInternet('error');
      setServer('pending');
      setCameras('pending');
      setRetrying(false);
      return;
    }
    setInternet('success');

    // ── 2. MOMENTS SERVER CHECK (up to 3 attempts) ───────────────────
    setServer('checking');
    setServerAttempt(1);

    let srvResult: HealthCheckResult = { ok: false, message: '', attempts: 0 };
    for (let attempt = 1; attempt <= 3; attempt++) {
      if (abortRef.current) {
        setRetrying(false);
        return;
      }
      setServerAttempt(attempt);
      try {
        srvResult = await window.bumper.checkServer(1);
        if (srvResult.ok) break;
      } catch {
        srvResult = { ok: false, message: 'Failed', attempts: attempt };
      }
      if (attempt < 3) {
        await new Promise((r) => setTimeout(r, 1200));
      }
    }

    if (abortRef.current) {
      setRetrying(false);
      return;
    }

    if (!srvResult.ok) {
      setServer('error');
      setCameras('pending');
      setRetrying(false);
      return;
    }
    setServer('success');

    // ── 3. CAMERAS CHECK (active states) ─────────────────────────────
    setCameras('checking');

    let cfg = { total: 2, cameras: [] as { id: string; name: string }[] };
    try {
      cfg = await window.bumper.getCameraConfig();
    } catch {
      cfg = { total: 2, cameras: [] };
    }
    const expected = cfg.total > 0 ? cfg.total : 2;
    setCamerasExpected(expected);

    // Poll live camera readiness
    const deadline = Date.now() + 8000;
    let ready = 0;
    while (Date.now() < deadline && !abortRef.current) {
      ready = cfg.cameras.filter((c) => readiness[c.id]?.ready).length;
      setCameraCount(ready);
      if (ready >= expected) {
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    if (abortRef.current) {
      setRetrying(false);
      return;
    }

    if (ready === expected && expected > 0) {
      setCameras('success');
    } else if (ready > 0) {
      setCameras('warning');
    } else {
      setCameras('error');
    }

    setRetrying(false);
  }, [retrying, readiness]);

  // Initial trigger when modal opens
  const hasRun = useRef(false);
  useEffect(() => {
    if (isOpen && !hasRun.current) {
      hasRun.current = true;
      void runChecks();
    }
    if (!isOpen) {
      hasRun.current = false;
      abortRef.current = true;
    }
  }, [isOpen, runChecks]);

  const handleClose = () => {
    abortRef.current = true;
    setIsOpen(false);
    onClose();
  };

  const handleRetry = () => {
    void runChecks();
  };

  if (!isOpen || (dontShow && !open)) {
    return null;
  }

  // Derive labels and sub-labels matching reference
  const internetLabel =
    internet === 'success'
      ? 'Internet Connected'
      : internet === 'error'
      ? 'No Internet Connection'
      : internet === 'checking'
      ? `Checking Internet (attempt ${internetAttempt}/9)...`
      : 'Internet Check';

  const internetSub =
    internet === 'error' ? 'Please connect to the internet' : undefined;

  const serverLabel =
    server === 'success'
      ? 'Moments Server Running'
      : server === 'error'
      ? 'Moments Server Failed to Start'
      : server === 'checking'
      ? `Starting Server (attempt ${serverAttempt}/3)...`
      : 'Moments Server';

  const serverSub = server === 'error' ? CONTACT_SUPPORT : undefined;

  const camerasLabel =
    cameras === 'success'
      ? 'All Cameras Connected'
      : cameras === 'warning'
      ? `${cameraCount}/${camerasExpected} Cameras Connected`
      : cameras === 'error'
      ? 'No Cameras Connected'
      : cameras === 'checking'
      ? 'Checking Cameras...'
      : 'Cameras';

  const camerasSub =
    cameras === 'warning'
      ? 'Moments running on fewer cameras, connect the rest'
      : cameras === 'error'
      ? 'Insufficient cameras'
      : undefined;

  const rows = [internet, server, cameras];
  const allPassed = rows.every((s) => s === 'success');
  const finished =
    !retrying &&
    (rows.includes('error') || rows.every((s) => s === 'success' || s === 'warning'));

  return (
    <Dialog
      open={isOpen}
      onClose={handleClose}
      slotProps={{
        paper: {
          sx: {
            width: 'clamp(520px, 40vw, 680px)',
            backgroundColor: '#1b1c1e',
            color: '#ffffff',
            borderRadius: '12px',
            boxShadow: '0 12px 36px rgba(0,0,0,0.65)',
            border: '1px solid #2e3034',
            p: 0,
            overflow: 'hidden',
          },
        },
      }}
    >
      {/* Title Header */}
      <Box
        sx={{
          backgroundColor: '#232529',
          px: 3,
          py: 2,
          borderBottom: '1px solid #2e3034',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <Typography
          variant="h6"
          sx={{
            fontWeight: 700,
            fontSize: '1.15rem',
            letterSpacing: '0.5px',
            color: '#f0f0f0',
          }}
        >
          Moments Server Health
        </Typography>
        {retrying && (
          <CircularProgress size={18} sx={{ color: '#25AE88' }} />
        )}
      </Box>

      {/* Body Content */}
      <Box
        sx={{
          px: 3,
          pt: 3,
          pb: 2.5,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <Box
          sx={{
            width: '100%',
            maxWidth: '520px',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.6rem',
          }}
        >
          {/* ROW 1: INTERNET */}
          <CheckRow
            icon={<NetworkIcon color={ICON_COLORS[internet]} />}
            state={internet}
            label={internetLabel}
            sub={internetSub}
          />

          {/* ROW 2: MOMENTS SERVER */}
          <CheckRow
            icon={<ServerIcon color={ICON_COLORS[server]} />}
            state={server}
            label={serverLabel}
            sub={serverSub}
          />

          {/* ROW 3: CAMERAS */}
          <CheckRow
            icon={<VideocamIcon color={ICON_COLORS[cameras]} />}
            state={cameras}
            label={camerasLabel}
            sub={camerasSub}
          />
        </Box>

        {/* Action Buttons: Retry if failed, Ok when finished */}
        {finished && (
          <Box
            sx={{
              display: 'flex',
              gap: '1rem',
              mt: 2.5,
              alignSelf: 'center',
            }}
          >
            {!allPassed && (
              <Button
                onClick={handleRetry}
                variant="outlined"
                disabled={retrying}
                sx={{
                  color: '#f5a623',
                  borderColor: '#f5a623',
                  fontWeight: 600,
                  px: 3,
                  '&:hover': {
                    borderColor: '#ffbe4d',
                    backgroundColor: 'rgba(245, 166, 35, 0.08)',
                  },
                }}
              >
                Retry
              </Button>
            )}

            <Button
              onClick={handleClose}
              variant="contained"
              sx={{
                backgroundColor: allPassed ? '#25AE88' : '#393c42',
                color: '#ffffff',
                fontWeight: 600,
                px: 3.5,
                '&:hover': {
                  backgroundColor: allPassed ? '#209675' : '#454950',
                },
              }}
            >
              Ok
            </Button>
          </Box>
        )}

        {/* Don't show again checkbox */}
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            mt: 2,
            opacity: 0.85,
          }}
        >
          <Checkbox
            size="small"
            checked={dontShow}
            onChange={(e) => setDontShow(e.target.checked)}
            sx={{
              color: '#6b6b6b',
              '&.Mui-checked': { color: '#25AE88' },
            }}
          />
          <Typography sx={{ fontSize: '0.85rem', color: '#c0c0c0' }}>
            Don't Show Again in this session
          </Typography>
        </Box>
      </Box>
    </Dialog>
  );
}
