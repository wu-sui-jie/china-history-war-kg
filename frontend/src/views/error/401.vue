<!--
  401 未授权错误页面
  
  功能: 显示401未授权错误
    - 用户未登录访问需要登录的页面时触发
    - 提供刷新和返回按钮
-->
<template>
  <lay-card class="error-page">
    <lay-exception status="401" title="401" describe="抱歉，你无权访问该页面">
      <template #extra>
        <lay-button>刷新</lay-button>
        <lay-button type="primary">返回</lay-button>
      </template>
    </lay-exception>
  </lay-card>
</template>

<style>
.error-page {
  padding-top: 200px;
  padding-bottom: 200px;
  margin: 10px;
}
</style>