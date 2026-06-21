# Send your first notification

This tutorial takes a fresh ChannelWatch install from the end of [Getting started](getting-started.md) to one successful test notification. You will configure one provider, save the setting, run a safe test alert, and confirm the message arrives.

Pushover is the main example because ChannelWatch has a simple dedicated Pushover field. Discord is a good free alternative if you already have a Discord server. Email also works, but SMTP setup often has more provider rules.

## Prerequisites

Before you start:

1. Finish [Getting started](getting-started.md) so ChannelWatch is running and you can open the web UI.
2. Sign in with an operator account, or use an account that can edit Settings and run Diagnostics tests.
3. Have one notification destination ready:
   - Pushover: your user key and application token.
   - Discord: a Discord webhook URL, or the webhook ID and token.
   - Email: SMTP credentials and the mailbox that should receive alerts.
4. Keep real tokens private. The examples below use placeholders such as `your_user_key` and `your_app_token`.

## Choose a notification provider

1. Start with Pushover if you can.

   ChannelWatch stores the Pushover field as the part after `pover://`. Enter it in this shape:

   ```text
   your_user_key@your_app_token
   ```

2. Use Discord instead if you want a free team channel.

   ChannelWatch accepts either of these shapes in the Discord field:

   ```text
   your_webhook_id/your_webhook_token
   ```

   ```text
   https://discord.com/api/webhooks/your_webhook_id/your_webhook_token
   ```

3. Use email only if you already know your SMTP details.

   Email needs both an SMTP value and a recipient address. Some mail services require app passwords, even when your normal password works in a browser.

For the full list of supported destination fields and URL shapes, see [Apprise provider reference](../reference/apprise-providers.md).

## Configure the provider in Settings

1. Open the ChannelWatch web UI.
2. Select **Settings**.
3. Select the **Notifications** tab.
4. Stay on the **Global** notification scope unless you only want this provider for one DVR.
5. Turn on **Pushover**.
6. In the Pushover URL field, enter your Pushover value:

   ```text
   your_user_key@your_app_token
   ```

7. Leave the default notification templates alone for this first test.

   Templates change message text, not provider delivery. After your first alert works, use [Notification template variable reference](../reference/templates.md) to learn which placeholders are available.

8. Select **Save**.
9. Wait for the saved message and the page reload.

   ChannelWatch saves the setting, signals the core process to restart, then reloads the UI. The restart matters because notification providers are loaded by the monitoring core.

## Test the alert from the UI

1. Select **Diagnostics**.
2. Find the **Tests** card.
3. In the **Notifications** section, find **Test Channel Watching Alert**.
4. Select its **Run** button.

   This is the UI test action for sending a safe notification. The frontend sends it to the backend as `POST /api/run_test/Test_Channel_Watching_Alert`.

5. Wait for the button result.
6. If the result shows a pass, go to your Pushover app and look for the ChannelWatch test message.

You can also run **Test Disk Space Alert** if you want a disk style message. For the first tutorial, one passing notification test is enough.

## Verify delivery

1. Open the destination you configured.
2. Confirm that one ChannelWatch message arrived.
3. Check that the title and body are readable.
4. Return to ChannelWatch and confirm the Diagnostics test stayed marked as passed.
5. Keep the provider enabled if you want real alerts to use it.

You have now sent your first ChannelWatch notification.

## Troubleshoot if it fails

1. Confirm the provider value has no placeholder text left in it.

   For Pushover, `your_user_key@your_app_token` must be replaced with your real private values in the UI.

2. Save Settings again if you changed the provider value.
3. Wait for the UI reload before testing again.
4. Check **Diagnostics** for configured providers. If none are listed, ChannelWatch did not load your provider value.
5. Check `/config/channelwatch.log` for provider add failures, delivery errors, or restart problems.
6. Try Discord if you need a quick second provider to compare against Pushover.
7. Use [Troubleshoot notification delivery](../how-to/troubleshoot-notifications.md) for focused failure checks.

## What you learned

1. Provider fields live under **Settings** > **Notifications**.
2. Pushover uses `user_key@app_token` in ChannelWatch, not a full `pover://` URL.
3. Saving Settings restarts the core so notification providers reload.
4. The Diagnostics **Run** button sends a safe test notification through the same provider path used by alerts.
5. Provider setup, template text, and delivery troubleshooting each have their own docs:
   - [Apprise provider reference](../reference/apprise-providers.md)
   - [Notification template variable reference](../reference/templates.md)
   - [Troubleshoot notification delivery](../how-to/troubleshoot-notifications.md)
