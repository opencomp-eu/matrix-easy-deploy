Please see the [Matrix hookshot docs](https://matrix-org.github.io) for full instructions on configuring hookshot.

## Github quick start

[Here's the docs](https://matrix-org.github.io/matrix-hookshot/latest/setup/github.html) for Github specifically.

In short, you must:
- [Create a Github App](https://github.com/settings/apps/new)
- Create a webhook secret
- Create private key for the app and place it in `modules/hookshot/hookshot/github-key.pem`
- Install the app for your github user
- Update `modules/hookshot/hookshot/config.yml` with the app id and webhook secret
- Send a direct messsage to `@hookshot:yourmatrixserver.com` with contents `github setpersonaltoken %your-token%`

Now, you can say `!hookshot github repo <repo url>` to get updates from the hookshot bot on the events you chose in the Github App. 