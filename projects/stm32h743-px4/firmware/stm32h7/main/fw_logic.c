
/* fw_logic.c - deterministic firmware logic: identical source compiles into
 * the target firmware and the host regression build. */
#include "fw_logic.h"

fw_boot_t fw_boot_next(fw_boot_t s)
{
    switch (s) {
    case FW_BOOT_START:     return FW_BOOT_LDO_EN;
    case FW_BOOT_LDO_EN:    return FW_BOOT_PIN_INIT;
    case FW_BOOT_PIN_INIT:  return FW_BOOT_CONSOLE;
    case FW_BOOT_CONSOLE:   return FW_BOOT_SELFTEST;
    case FW_BOOT_SELFTEST:  return FW_BOOT_RUN;
    case FW_BOOT_RUN:
    default:                return FW_BOOT_RUN;
    }
}

int fw_debounce_stable(const uint8_t *samples, size_t n)
{
    if (n == 0) return 0;
    for (size_t i = 0; i < n; i++)
        if (samples[i] != samples[0]) return 0;
    return 1;
}

uint8_t fw_spi_loopback(const uint8_t *tx, uint8_t *rx, size_t n)
{
    /* soft loopback: full-duplex transfer returns what was shifted out */
    for (size_t i = 0; i < n; i++)
        rx[i] = tx[i];
    return (n > 0) ? rx[n - 1] : 0;
}

uint32_t fw_led_blink_ms(uint32_t period_ms, uint8_t duty_pct)
{
    if (period_ms == 0) return 0;
    if (duty_pct > 100) duty_pct = 100;
    return (period_ms * duty_pct) / 100;
}
