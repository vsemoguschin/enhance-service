module.exports = {
  apps: [
    {
      name: 'enhance-service-a',
      cwd: '/opt/codex/enhance-service',
      script: 'scripts/start.sh',
      interpreter: '/bin/bash',
      env: {
        ENHANCE_SLOT_OVERRIDE: 'a',
        ENHANCE_HOST_OVERRIDE: '127.0.0.1',
        ENHANCE_PORT_OVERRIDE: '8012',
      },
      autorestart: true,
      kill_timeout: 300000,
    },
    {
      name: 'enhance-service-b',
      cwd: '/opt/codex/enhance-service',
      script: 'scripts/start.sh',
      interpreter: '/bin/bash',
      env: {
        ENHANCE_SLOT_OVERRIDE: 'b',
        ENHANCE_HOST_OVERRIDE: '127.0.0.1',
        ENHANCE_PORT_OVERRIDE: '8013',
      },
      autorestart: true,
      kill_timeout: 300000,
    },
  ],
};
