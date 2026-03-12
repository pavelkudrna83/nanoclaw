import { describe, it, expect, beforeEach, vi } from 'vitest';

import { processMessageIpc } from './ipc.js';
import { RegisteredGroup } from './types.js';

const MAIN_GROUP: RegisteredGroup = {
  name: 'Main',
  folder: 'telegram_main',
  trigger: 'always',
  added_at: '2024-01-01T00:00:00.000Z',
  isMain: true,
};

const OTHER_GROUP: RegisteredGroup = {
  name: 'Other',
  folder: 'other-group',
  trigger: '@Gimi',
  added_at: '2024-01-01T00:00:00.000Z',
};

let groups: Record<string, RegisteredGroup>;
let sendMessage: ReturnType<typeof vi.fn>;
let sendVoice: ReturnType<typeof vi.fn>;
let deps: Parameters<typeof processMessageIpc>[3];

beforeEach(() => {
  groups = {
    'tg:123': MAIN_GROUP,
    'tg:456': OTHER_GROUP,
  };
  sendMessage = vi.fn().mockResolvedValue(undefined);
  sendVoice = vi.fn().mockResolvedValue(undefined);
  deps = {
    sendMessage,
    sendVoice,
    registeredGroups: () => groups,
  };
});

// --- Text messages (voice: false/undefined) ---

describe('processMessageIpc — text messages', () => {
  it('sends text message from main group', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:456', text: 'Hello' },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('sent');
    expect(sendMessage).toHaveBeenCalledWith('tg:456', 'Hello');
    expect(sendVoice).not.toHaveBeenCalled();
  });

  it('sends text message from own group', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:456', text: 'Hi' },
      'other-group',
      false,
      deps,
    );

    expect(result).toBe('sent');
    expect(sendMessage).toHaveBeenCalledWith('tg:456', 'Hi');
    expect(sendVoice).not.toHaveBeenCalled();
  });

  it('sends text when voice is explicitly false', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:123', text: 'Text only', voice: false },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('sent');
    expect(sendMessage).toHaveBeenCalledWith('tg:123', 'Text only');
    expect(sendVoice).not.toHaveBeenCalled();
  });
});

// --- Voice messages (voice: true) ---

describe('processMessageIpc — voice messages', () => {
  it('sends voice message when voice flag is true', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:123', text: 'Focus blok končí za 10 minut', voice: true },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('voice');
    expect(sendVoice).toHaveBeenCalledWith('tg:123', 'Focus blok končí za 10 minut');
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('sends voice message from own group', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:456', text: 'Alert', voice: true },
      'other-group',
      false,
      deps,
    );

    expect(result).toBe('voice');
    expect(sendVoice).toHaveBeenCalledWith('tg:456', 'Alert');
    expect(sendMessage).not.toHaveBeenCalled();
  });
});

// --- Authorization ---

describe('processMessageIpc — authorization', () => {
  it('blocks non-main group sending to another group', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:123', text: 'Unauthorized' },
      'other-group',
      false,
      deps,
    );

    expect(result).toBe('unauthorized');
    expect(sendMessage).not.toHaveBeenCalled();
    expect(sendVoice).not.toHaveBeenCalled();
  });

  it('blocks non-main group sending voice to another group', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:123', text: 'Unauthorized voice', voice: true },
      'other-group',
      false,
      deps,
    );

    expect(result).toBe('unauthorized');
    expect(sendMessage).not.toHaveBeenCalled();
    expect(sendVoice).not.toHaveBeenCalled();
  });

  it('blocks sending to unregistered JID from non-main', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:999', text: 'Unknown target' },
      'other-group',
      false,
      deps,
    );

    expect(result).toBe('unauthorized');
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it('main group can send to unregistered JID', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:999', text: 'Main can send anywhere' },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('sent');
    expect(sendMessage).toHaveBeenCalledWith('tg:999', 'Main can send anywhere');
  });

  it('main group can send voice to unregistered JID', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:999', text: 'Voice anywhere', voice: true },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('voice');
    expect(sendVoice).toHaveBeenCalledWith('tg:999', 'Voice anywhere');
  });
});

// --- Skipped (invalid data) ---

describe('processMessageIpc — skipped messages', () => {
  it('skips non-message type', async () => {
    const result = await processMessageIpc(
      { type: 'other', chatJid: 'tg:123', text: 'Ignored' },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('skipped');
    expect(sendMessage).not.toHaveBeenCalled();
    expect(sendVoice).not.toHaveBeenCalled();
  });

  it('skips message without chatJid', async () => {
    const result = await processMessageIpc(
      { type: 'message', text: 'No JID' },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('skipped');
  });

  it('skips message without text', async () => {
    const result = await processMessageIpc(
      { type: 'message', chatJid: 'tg:123' },
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('skipped');
  });

  it('skips empty object', async () => {
    const result = await processMessageIpc(
      {},
      'telegram_main',
      true,
      deps,
    );

    expect(result).toBe('skipped');
  });
});
