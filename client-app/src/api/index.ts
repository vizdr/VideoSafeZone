// The app's one session and one API client. Tests build their own from the classes instead.
import { API } from "../config";
import { CognitoSession } from "../auth/cognito";
import { ApiClient } from "./client";
import { makeEndpoints } from "./endpoints";

export const session = new CognitoSession();

export const endpoints = makeEndpoints(new ApiClient({
  base: API,
  token: () => session.idToken(),
  refresh: () => session.refresh(),
}));
