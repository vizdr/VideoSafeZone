<script setup lang="ts">
// Every billing screen says billing is a demo (DEALER-PANEL-PLAN.md D9). There are no card or bank
// fields anywhere, not even fake ones.
import Message from "openvue/message";
</script>

<template>
  <Message severity="warn" size="small" variant="simple" data-test="demo-banner">Demo billing: no payment is taken.</Message>
</template>
