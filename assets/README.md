# Private report assets

The club's `template.xlsx` and `instructions.pdf` are intentionally not distributed.
Supply authorized copies here for local report generation. Production stores them
in `/var/lib/trainings-bot/assets` and links each release's `assets` directory there.

The renderer expects the existing club-specific cell layout. An arbitrary workbook
is not a drop-in replacement. Public CI generates a synthetic workbook in a temporary
checkout; it does not verify fidelity to the private club form.
