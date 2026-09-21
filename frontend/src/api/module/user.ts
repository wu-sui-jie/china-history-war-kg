import Http from '../http';

export const menu = function() {
    return Http.get('/user/menu')
}

export const permission = function() {
    return Http.get('/user/permission')
}

