import { configureStore, createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type { AppState, CameraReadiness, Notice } from '../../../backend/shared/types';

const slice = createSlice({
  name: 'app',
  initialState: {
    state: null as AppState | null,
    notice: null as Notice | null,
    previews: {} as Record<string, string>,
    readiness: {} as Record<string, CameraReadiness>,
    diskFreeGb: null as number | null,
  },
  reducers: {
    setState(s, a: PayloadAction<AppState>) {
      s.state = a.payload;
    },
    setNotice(s, a: PayloadAction<Notice | null>) {
      s.notice = a.payload;
    },
    setPreviewFrame(s, a: PayloadAction<{ cameraId: string; dataUrl: string }>) {
      s.previews[a.payload.cameraId] = a.payload.dataUrl;
    },
    setReadiness(s, a: PayloadAction<Record<string, CameraReadiness>>) {
      s.readiness = a.payload;
    },
    setDiskFreeGb(s, a: PayloadAction<number>) {
      s.diskFreeGb = a.payload;
    },
  },
});

export const { setState, setNotice, setPreviewFrame, setReadiness, setDiskFreeGb } = slice.actions;
export const store = configureStore({ reducer: slice.reducer });
export type RootState = ReturnType<typeof store.getState>;
