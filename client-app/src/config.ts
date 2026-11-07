// This page's cloud-side configuration: the client's own constants, never the Pi's adapter.env
// (CLAUDE.md). The same values the old single-file page (client/index.html) used.
export const API = "https://svfvg7xh9g.execute-api.eu-central-1.amazonaws.com/prod";
export const COGNITO_REGION = "eu-central-1";
// The app client vsz-web of the user pool vsz-users (DEALER-PANEL-PLAN.md D1, Phase 3.3). It was
// kvs-demo-users' kvs-demo-web-client (1r0n2cor1pgqvahfogs6cfno7s), the pool shared with VMS.
// deploy.sh page refuses to publish while kvs-demo-api's authorizer doesn't accept this pool.
export const COGNITO_CLIENT_ID = "4g53lfqkkriopjntkgf240edte";
