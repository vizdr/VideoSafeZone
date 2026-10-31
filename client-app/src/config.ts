// This page's cloud-side configuration: the client's own constants, never the Pi's adapter.env
// (CLAUDE.md). The same values as client/index.html, which stays live until the cutover.
export const API = "https://svfvg7xh9g.execute-api.eu-central-1.amazonaws.com/prod";
export const COGNITO_REGION = "eu-central-1";
export const COGNITO_CLIENT_ID = "1r0n2cor1pgqvahfogs6cfno7s";

// The single-file page, served beside this app until the cutover (WEB-PANEL-PLAN.md D2).
export const OLD_PAGE = "../index.html";
